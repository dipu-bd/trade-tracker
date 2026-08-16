from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import copysign, erf, exp, log, sqrt
from statistics import fmean, pstdev

EULER_MASCHERONI = 0.5772156649015329


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def normal_ppf(probability: float) -> float:
    """Inverse normal CDF, Acklam's rational approximation.

    Hand-rolled because `analytics` and its neighbours stay dependency-free, and scipy for one
    function would be the largest wheel in the image.
    """
    if probability <= 0.0:
        return -float("inf")
    if probability >= 1.0:
        return float("inf")

    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]

    low, high = 0.02425, 1 - 0.02425
    if probability < low:
        q = sqrt(-2 * log(probability))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if probability > high:
        q = sqrt(-2 * log(1 - probability))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )

    q = probability - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


@dataclass(frozen=True, slots=True)
class DeflatedSharpe:
    observed: float
    expected_max: float
    deflated: float
    probability: float
    trials: int
    skew: float
    kurtosis: float

    @property
    def is_significant(self) -> bool:
        return self.probability >= 0.95

    def as_dict(self) -> dict[str, float | int | bool]:
        return {
            "observed_sharpe": round(self.observed, 4),
            "expected_max_sharpe": round(self.expected_max, 4),
            "deflated_sharpe": round(self.deflated, 4),
            "probability": round(self.probability, 4),
            "trials": self.trials,
            "skew": round(self.skew, 4),
            "kurtosis": round(self.kurtosis, 4),
            "significant": self.is_significant,
        }


def moments(returns: Sequence[float]) -> tuple[float, float]:
    """Sample skewness and kurtosis, both needed because returns are not normal."""
    if len(returns) < 4:
        return 0.0, 3.0

    mean = fmean(returns)
    deviation = pstdev(returns)
    if deviation == 0:
        return 0.0, 3.0

    n = len(returns)
    skew = sum(((value - mean) / deviation) ** 3 for value in returns) / n
    kurtosis = sum(((value - mean) / deviation) ** 4 for value in returns) / n
    return skew, kurtosis


def expected_max_sharpe(trials: int, variance: float = 1.0) -> float:
    """The Sharpe you expect from the best of `trials` random strategies.

    This is the whole point of deflation: try enough configurations and a flattering Sharpe is
    guaranteed, so the bar rises with the number of attempts.
    """
    if trials <= 1:
        return 0.0
    sigma = sqrt(max(variance, 1e-12))
    first = normal_ppf(1 - 1.0 / trials)
    second = normal_ppf(1 - 1.0 / (trials * exp(1.0)))
    return sigma * ((1 - EULER_MASCHERONI) * first + EULER_MASCHERONI * second)


def deflated_sharpe(
    returns: Sequence[float],
    observed_sharpe: float,
    trials: int,
    *,
    benchmark: float | None = None,
) -> DeflatedSharpe:
    """Bailey & Lopez de Prado's DSR.

    An impressive backtest is trivially obtained after a handful of configurations, so every
    reported Sharpe is deflated for how many were tried and for non-normal returns.
    """
    n = len(returns)
    skew, kurtosis = moments(returns)

    if n < 4:
        return DeflatedSharpe(observed_sharpe, 0.0, 0.0, 0.0, trials, skew, kurtosis)

    threshold = benchmark if benchmark is not None else expected_max_sharpe(trials)

    denominator = 1.0 - skew * observed_sharpe + (kurtosis - 1.0) / 4.0 * observed_sharpe**2
    if denominator <= 0:
        return DeflatedSharpe(observed_sharpe, threshold, 0.0, 0.0, trials, skew, kurtosis)

    statistic = (observed_sharpe - threshold) * sqrt(n - 1) / sqrt(denominator)
    return DeflatedSharpe(
        observed=observed_sharpe,
        expected_max=threshold,
        deflated=statistic,
        probability=normal_cdf(statistic),
        trials=trials,
        skew=skew,
        kurtosis=kurtosis,
    )


def minimum_track_record_length(
    observed_sharpe: float,
    skew: float,
    kurtosis: float,
    target: float = 0.0,
    confidence: float = 0.95,
) -> float:
    """How many observations are needed before a Sharpe this size means anything."""
    if observed_sharpe <= target:
        return float("inf")
    denominator = (observed_sharpe - target) ** 2
    numerator = 1.0 - skew * observed_sharpe + (kurtosis - 1.0) / 4.0 * observed_sharpe**2
    return 1.0 + numerator / denominator * normal_ppf(confidence) ** 2


def probability_of_backtest_overfitting(
    performance: Sequence[Sequence[float]], splits: int = 8
) -> float:
    """PBO by combinatorially symmetric cross-validation.

    `performance[i][j]` is strategy j's return in period i. The question it answers is: when the
    configuration that looked best in-sample is run out-of-sample, how often is it below median?
    A PBO near 0.5 means the selection procedure has learned nothing.
    """
    periods = len(performance)
    if periods < 4 or not performance[0]:
        return 0.0

    strategies = len(performance[0])
    if strategies < 2:
        return 0.0

    splits = min(splits, periods)
    if splits % 2 == 1:
        splits -= 1
    if splits < 2:
        return 0.0

    chunks = _chunk(list(range(periods)), splits)
    half = splits // 2
    logits: list[float] = []

    for train_ids in combinations(range(splits), half):
        train_rows = [row for index in train_ids for row in chunks[index]]
        test_rows = [
            row for index in range(splits) if index not in train_ids for row in chunks[index]
        ]
        if not train_rows or not test_rows:
            continue

        in_sample = [_total(performance, train_rows, j) for j in range(strategies)]
        out_sample = [_total(performance, test_rows, j) for j in range(strategies)]

        best = max(range(strategies), key=lambda j: in_sample[j])
        rank = sorted(out_sample).index(out_sample[best]) + 1
        relative = rank / (strategies + 1)
        relative = min(max(relative, 1e-6), 1 - 1e-6)
        logits.append(log(relative / (1 - relative)))

    if not logits:
        return 0.0
    return sum(1 for value in logits if value <= 0) / len(logits)


def _chunk(items: list[int], count: int) -> list[list[int]]:
    size = len(items) // count
    return [items[index * size : (index + 1) * size] for index in range(count)]


def _total(performance: Sequence[Sequence[float]], rows: Sequence[int], column: int) -> float:
    return sum(performance[row][column] for row in rows)


@dataclass(frozen=True, slots=True)
class Fold:
    train: list[int]
    test: list[int]


def purged_kfold(
    count: int, folds: int = 5, embargo: float = 0.01, horizon: int = 1
) -> Iterator[Fold]:
    """Purged k-fold with an embargo, per Lopez de Prado.

    Plain k-fold leaks on time series: a training label overlapping the test window means the
    model has seen its own answer. Purging removes the overlap, and the embargo drops the window
    immediately after the test fold, where serial correlation still carries information.
    """
    if count < folds or folds < 2:
        return

    gap = max(1, int(count * embargo))
    size = count // folds

    for index in range(folds):
        start = index * size
        stop = count if index == folds - 1 else start + size
        test = list(range(start, stop))

        purged_from = max(0, start - horizon)
        purged_to = min(count, stop + horizon + gap)
        train = [row for row in range(count) if row < purged_from or row >= purged_to]

        if train and test:
            yield Fold(train=train, test=test)


def walk_forward(count: int, folds: int = 5, minimum: int = 60) -> Iterator[Fold]:
    """Anchored walk-forward: train always starts at the beginning and grows.

    The headline evaluation, because a single train/test split says nothing about whether a
    strategy kept working as the window moved.
    """
    if count <= minimum:
        return

    remaining = count - minimum
    step = max(1, remaining // folds)

    for index in range(folds):
        split = minimum + index * step
        stop = min(count, split + step)
        if split >= count or split >= stop:
            break
        yield Fold(train=list(range(split)), test=list(range(split, stop)))


MAX_T = 1000.0


def t_statistic(values: Sequence[float]) -> float:
    """How many standard errors a mean sits from zero. Used to judge whether an IC is real.

    Zero dispersion around a non-zero mean is the *strongest* evidence, not the weakest, so it
    saturates rather than returning zero — reporting it as no-evidence would de-weight a
    perfectly consistent signal to nothing. Capped to stay JSON-serialisable.
    """
    if len(values) < 2:
        return 0.0

    mean = fmean(values)
    deviation = pstdev(values)
    if deviation == 0:
        return 0.0 if mean == 0 else copysign(MAX_T, mean)

    return mean / (deviation / sqrt(len(values)))
