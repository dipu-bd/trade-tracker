from dataclasses import dataclass

MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class Price:
    prompt: float
    completion: float
    cached_prompt: float = 0.0


PRICES: dict[str, Price] = {
    "gpt-5": Price(1.25, 10.00, 0.125),
    "gpt-5-mini": Price(0.25, 2.00, 0.025),
    "gpt-4.1": Price(2.00, 8.00, 0.50),
    "gpt-4.1-mini": Price(0.40, 1.60, 0.10),
    "o4-mini": Price(1.10, 4.40, 0.275),
    "claude-opus-4": Price(15.00, 75.00, 1.50),
    "claude-sonnet-4": Price(3.00, 15.00, 0.30),
    "claude-haiku-4": Price(0.80, 4.00, 0.08),
    "gemini-2.5-pro": Price(1.25, 10.00, 0.31),
    "gemini-2.5-flash": Price(0.30, 2.50, 0.075),
    "gemini-2.0-flash": Price(0.10, 0.40, 0.025),
    "deepseek-chat": Price(0.27, 1.10, 0.07),
    "deepseek-reasoner": Price(0.55, 2.19, 0.14),
    "llama-3.3-70b": Price(0.59, 0.79),
    "qwen-2.5-72b": Price(0.35, 0.40),
}

FREE_MARKERS = (":free", "ollama", "localhost", "127.0.0.1")


def estimate(model: str, prompt_tokens: int, completion_tokens: int, cached: int = 0) -> float:
    """Cost in USD. Information for the dashboard, never a gate on a decision.

    An unknown model prices at zero rather than guessing: a fabricated number shown next to real
    ones is worse than an obvious blank, and spend ceilings belong to the provider subscription.
    """
    price = lookup(model)
    if price is None:
        return 0.0

    billable_prompt = max(0, prompt_tokens - cached)
    return (
        billable_prompt * price.prompt
        + cached * price.cached_prompt
        + completion_tokens * price.completion
    ) / MILLION


def lookup(model: str) -> Price | None:
    key = model.lower().strip()
    if any(marker in key for marker in FREE_MARKERS):
        return Price(0.0, 0.0)

    if key in PRICES:
        return PRICES[key]

    # OpenRouter and friends prefix with a vendor: "google/gemini-2.5-flash".
    tail = key.rsplit("/", 1)[-1]
    if tail in PRICES:
        return PRICES[tail]

    for name, price in PRICES.items():
        if tail.startswith(name):
            return price

    return None


def estimate_tokens(text: str) -> int:
    """Rough token count for the rate governor's budget, deliberately biased high.

    The governor needs a number before the call, and under-counting means a 429 while
    over-counting only means waiting slightly longer than necessary.
    """
    return max(1, len(text) // 3)
