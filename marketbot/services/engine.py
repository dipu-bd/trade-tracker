"""The scan loop: gather data, decide, execute, notify.

Order matters and is deliberate — exits are evaluated before entries so freed
capital is available the same run, and the daily-loss breaker is checked
before any new risk is taken.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketbot.advisors import AdvisorResult, apply_verdicts, build_brief
from marketbot.db import (
    AssetClass,
    EventType,
    ExitReason,
    Instrument,
    LlmAdvice,
    Portfolio,
    Position,
    Regime,
    ScanRun,
    Signal,
    Sleeve,
)
from marketbot.dto.market import Candidate
from marketbot.services import scanner, strategy
from marketbot.services.strategy import (
    ActionPlan,
    ProposedEntry,
    ProposedExit,
    StopMove,
)

_log = logging.getLogger(__name__)

SLEEVE_CLASSES = {
    Sleeve.EQUITY: (AssetClass.STOCK, AssetClass.ETF),
    Sleeve.CRYPTO: (AssetClass.CRYPTO,),
    Sleeve.ALL: (AssetClass.STOCK, AssetClass.ETF, AssetClass.CRYPTO),
}


class EngineService:
    def __init__(self, ctx):
        self._ctx = ctx

    @property
    def db(self):
        return self._ctx.db

    @property
    def market(self):
        return self._ctx.market_data

    @property
    def portfolios(self):
        return self._ctx.portfolios

    # ----------------------------------------------------------------- #
    # Entry point
    # ----------------------------------------------------------------- #

    def run_scan(
        self,
        portfolio_id: int,
        sleeve: str = Sleeve.ALL,
        dry_run: bool = False,
    ) -> dict:
        with self.db.session() as session:
            portfolio = self.portfolios.get(session, portfolio_id)
            if portfolio is None:
                raise ValueError(f'No portfolio with id {portfolio_id}')
            if not portfolio.is_active:
                _log.info(f'Portfolio {portfolio.name!r} is paused; skipping scan')
                return {'skipped': 'portfolio paused'}

            run = ScanRun(
                portfolio_id=portfolio.id,
                sleeve=sleeve,
                dry_run=dry_run,
            )
            session.add(run)
            session.flush()

            try:
                summary = self._execute_run(session, portfolio, run, sleeve, dry_run)
            except Exception as e:  # noqa: BLE001 — record then re-raise
                run.error = str(e)[:500]
                run.finished_at = datetime.now(timezone.utc)
                _log.exception(f'Scan failed for {portfolio.name!r}')
                raise

            run.finished_at = datetime.now(timezone.utc)
            session.flush()

        # Notify outside the transaction — a mail failure must not roll back
        # a completed set of trades.
        if not dry_run:
            self._notify(summary)
        return summary

    # ----------------------------------------------------------------- #
    # The run itself
    # ----------------------------------------------------------------- #

    def _execute_run(
        self,
        session: Session,
        portfolio: Portfolio,
        run: ScanRun,
        sleeve: str,
        dry_run: bool,
    ) -> dict:
        classes = SLEEVE_CLASSES.get(sleeve, SLEEVE_CLASSES[Sleeve.ALL])

        instruments = self._instruments_in_scope(session, portfolio, sleeve)
        run.universe_size = len(instruments)

        open_positions = self.portfolios.open_positions(session, portfolio)
        held_ids = [p.instrument_id for p in open_positions]
        self.market.refresh_bars(session, instruments, priority_ids=held_ids)

        quotes = self.market.get_quotes(session, instruments)
        candidates = self._build_candidates(session, instruments, quotes)
        run.candidates = len(candidates)

        prices = {c.symbol: c.price for c in candidates.values()}
        for position in open_positions:
            prices.setdefault(position.instrument.symbol, position.avg_entry)

        regime = self._detect_regime(session, candidates)
        run.regime = regime

        plan = self._build_plan(
            session, portfolio, open_positions, candidates, prices, regime, classes
        )
        advice_rows = self._consult_advisor(
            session, portfolio, run, plan, open_positions, prices, candidates
        )

        self._record_signals(session, run, candidates, plan)

        if dry_run:
            return self._dry_run_summary(
                session, portfolio, run, plan, prices, open_positions,
                regime, advice_rows, candidates,
            )
        return self._apply_plan(
            session, portfolio, run, plan, prices, regime, advice_rows
        )

    # ----------------------------------------------------------------- #
    # Data gathering
    # ----------------------------------------------------------------- #

    def _instruments_in_scope(
        self, session: Session, portfolio: Portfolio, sleeve: str
    ) -> List[Instrument]:
        instruments = self.market.sync_universe(
            session,
            sleeve,
            enable_stocks=portfolio.enable_stocks,
            enable_etfs=portfolio.enable_etfs,
            enable_crypto=portfolio.enable_crypto,
        )
        by_id = {i.id: i for i in instruments}

        # Held names must be priced even if they have dropped out of the
        # universe, or a position would go unmanaged.
        for position in self.portfolios.open_positions(session, portfolio):
            by_id.setdefault(position.instrument_id, position.instrument)

        # The regime benchmark is always needed, whatever the sleeve settings.
        benchmark = self.benchmark_instrument(session)
        if benchmark is not None:
            by_id.setdefault(benchmark.id, benchmark)

        return list(by_id.values())

    def benchmark_instrument(self, session: Session) -> Optional[Instrument]:
        instrument = session.scalar(
            select(Instrument).where(
                Instrument.symbol == strategy.REGIME_BENCHMARK,
                Instrument.asset_class == AssetClass.ETF,
            )
        )
        if instrument is None:
            instrument = Instrument(
                symbol=strategy.REGIME_BENCHMARK,
                asset_class=AssetClass.ETF,
                name='Regime benchmark',
                sector='Broad Market',
            )
            session.add(instrument)
            session.flush()
        return instrument

    def _build_candidates(
        self,
        session: Session,
        instruments: Sequence[Instrument],
        quotes: Dict[str, object],
    ) -> Dict[str, Candidate]:
        candidates: Dict[str, Candidate] = {}
        for instrument in instruments:
            bars = self.market.get_bars(session, instrument)
            candidate = scanner.build_candidate(
                instrument, bars, quotes.get(instrument.symbol)
            )
            if candidate is not None:
                candidates[instrument.symbol] = candidate
        return candidates

    def _detect_regime(
        self, session: Session, candidates: Dict[str, Candidate]
    ) -> str:
        benchmark = self.benchmark_instrument(session)
        if benchmark is None:
            return Regime.NEUTRAL
        bars = self.market.get_bars(session, benchmark)
        return strategy.detect_regime(bars)

    # ----------------------------------------------------------------- #
    # Planning
    # ----------------------------------------------------------------- #

    def _build_plan(
        self,
        session: Session,
        portfolio: Portfolio,
        open_positions: List[Position],
        candidates: Dict[str, Candidate],
        prices: Dict[str, float],
        regime: str,
        classes: Tuple[str, ...],
    ) -> ActionPlan:
        plan = ActionPlan(regime=regime)
        now = datetime.now(timezone.utc)

        in_sleeve = [
            p for p in open_positions
            if p.instrument.asset_class in classes
        ]

        # --- Exits, and stop ratchets on whatever survives -------------
        exiting: List[Position] = []
        for position in in_sleeve:
            symbol = position.instrument.symbol
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            candidate = candidates.get(symbol)

            reason = strategy.evaluate_exit(
                portfolio, position, price, candidate, regime, now
            )
            if reason:
                plan.exits.append(ProposedExit(
                    position=position,
                    price=price,
                    reason=reason,
                    score=candidate.score if candidate else 0.0,
                ))
                exiting.append(position)
                continue

            atr = candidate.atr if candidate else position.atr_at_entry
            new_stop = strategy.next_stop(portfolio, position, price, atr, regime)
            if new_stop > position.stop_price + 1e-9:
                plan.stop_moves.append(StopMove(
                    position=position,
                    old_stop=position.stop_price,
                    new_stop=new_stop,
                ))

        # --- Daily loss circuit breaker --------------------------------
        equity = self.portfolios.equity(portfolio, open_positions, prices)
        if self._breaker_tripped(session, portfolio, equity):
            plan.halted = True
            plan.halt_reason = (
                f'Daily loss limit of {portfolio.daily_loss_pct:.1f}% reached'
            )
            plan.entries = []
            plan.exits = [
                ProposedExit(
                    position=p,
                    price=prices.get(p.instrument.symbol, p.avg_entry),
                    reason=ExitReason.RISK_HALT,
                )
                for p in open_positions
            ]
            plan.stop_moves = []
            return plan

        # --- Entries ----------------------------------------------------
        survivors = [p for p in open_positions if p not in exiting]
        self._plan_entries(
            portfolio, plan, candidates, prices, survivors, regime, classes, equity
        )
        return plan

    def _breaker_tripped(
        self, session: Session, portfolio: Portfolio, equity: float
    ) -> bool:
        if portfolio.daily_loss_pct <= 0:
            return False
        previous = self.portfolios.previous_snapshot(session, portfolio)
        baseline = previous.equity if previous else portfolio.initial_capital
        if baseline <= 0:
            return False
        change_pct = (equity - baseline) / baseline * 100
        return change_pct <= -portfolio.daily_loss_pct

    def _plan_entries(
        self,
        portfolio: Portfolio,
        plan: ActionPlan,
        candidates: Dict[str, Candidate],
        prices: Dict[str, float],
        survivors: List[Position],
        regime: str,
        classes: Tuple[str, ...],
        equity: float,
    ) -> None:
        regime_mult = strategy.regime_multiplier(regime)
        if regime_mult <= 0 and AssetClass.CRYPTO not in classes:
            return

        held_symbols = [p.instrument.symbol for p in survivors]
        sector_counts: Dict[str, int] = {}
        for position in survivors:
            sector = position.instrument.sector or 'Unknown'
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        crypto_exposure = self.portfolios.sleeve_exposure(
            survivors, prices, (AssetClass.CRYPTO,)
        )
        equity_exposure = self.portfolios.sleeve_exposure(
            survivors, prices, AssetClass.EQUITY_CLASSES
        )
        crypto_room = equity * (portfolio.crypto_max_pct / 100) - crypto_exposure
        equity_room = (
            equity * (1 - portfolio.crypto_max_pct / 100) - equity_exposure
        )

        # Cash is consumed as we go, so later entries see what earlier ones left.
        available_cash = portfolio.cash
        open_count = len(survivors)
        scores = {c.symbol: c.score for c in candidates.values()}

        pool = [
            c for c in candidates.values()
            if c.asset_class in classes
            and c.symbol != strategy.REGIME_BENCHMARK
        ]
        for candidate in scanner.rank(pool):
            if open_count >= portfolio.max_positions:
                # The book is full — see if this idea is clearly better.
                displaced = strategy.find_rotation(
                    portfolio, candidate, survivors, scores, prices
                )
                if displaced is None:
                    continue
                eligible, _ = strategy.is_entry_eligible(
                    portfolio, candidate, regime, held_symbols,
                    sector_counts, open_count - 1,
                )
                if not eligible:
                    continue
                price = prices.get(displaced.instrument.symbol, displaced.avg_entry)
                plan.exits.append(ProposedExit(
                    position=displaced,
                    price=price,
                    reason=ExitReason.ROTATION,
                    score=scores.get(displaced.instrument.symbol, 0.0),
                ))
                survivors.remove(displaced)
                held_symbols.remove(displaced.instrument.symbol)
                open_count -= 1
                available_cash += price * displaced.qty
                if displaced.instrument.asset_class == AssetClass.CRYPTO:
                    crypto_room += price * displaced.qty
                else:
                    equity_room += price * displaced.qty

            eligible, _ = strategy.is_entry_eligible(
                portfolio, candidate, regime, held_symbols,
                sector_counts, open_count,
            )
            if not eligible:
                continue

            is_crypto = candidate.asset_class == AssetClass.CRYPTO
            sleeve_room = crypto_room if is_crypto else equity_room
            if sleeve_room <= 0:
                continue

            # Crypto trades round the clock, so an equity risk-off regime does
            # not zero its budget — it halves it.
            mult = regime_mult
            if is_crypto:
                mult = max(regime_mult, 0.5) if regime != Regime.BEARISH else 0.5

            price = candidate.price
            stop = strategy.stop_for(portfolio, candidate, price)
            qty, r_value = strategy.size_position(
                portfolio, equity, available_cash, price, stop, mult,
                sleeve_room, self.portfolios.allows_fractional(candidate.asset_class),
            )
            if qty <= 0:
                continue

            notional = qty * price
            plan.entries.append(ProposedEntry(
                candidate=candidate,
                qty=qty,
                entry_price=price,
                stop_price=stop,
                target_price=price + portfolio.take_profit_r * r_value,
                r_value=r_value,
                atr=candidate.atr,
                max_hold_days=strategy.max_hold_days(portfolio, candidate.asset_class),
            ))

            open_count += 1
            held_symbols.append(candidate.symbol)
            sector = candidate.sector or 'Unknown'
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            available_cash -= notional
            if is_crypto:
                crypto_room -= notional
            else:
                equity_room -= notional

    # ----------------------------------------------------------------- #
    # Advisor
    # ----------------------------------------------------------------- #

    def _consult_advisor(
        self,
        session: Session,
        portfolio: Portfolio,
        run: ScanRun,
        plan: ActionPlan,
        open_positions: List[Position],
        prices: Dict[str, float],
        candidates: Dict[str, Candidate],
    ) -> List[dict]:
        config = self._ctx.config.advisor
        advisor = self._ctx.advisor
        if advisor is None or plan.halted:
            return []
        if not plan.entries and not open_positions:
            return []

        exiting = {x.position.instrument.symbol for x in plan.exits}
        holdings_brief = []
        holdings_map: Dict[str, Tuple[Position, float, float]] = {}
        for position in open_positions:
            symbol = position.instrument.symbol
            price = prices.get(symbol, position.avg_entry)
            candidate = candidates.get(symbol)
            score = candidate.score if candidate else 0.0
            holdings_map[symbol.upper()] = (position, price, score)
            if symbol in exiting:
                continue
            holdings_brief.append({
                'action': 'HOLD',
                'symbol': symbol,
                'asset_class': position.instrument.asset_class,
                'entry_price': round(position.avg_entry, 4),
                'price': round(price, 4),
                'stop_price': round(position.stop_price, 4),
                'r_multiple': round(position.r_multiple(price), 2),
                'current_score': round(score, 1),
                'held_days': _held_days(position),
            })

        equity = self.portfolios.equity(portfolio, open_positions, prices)
        brief = build_brief(
            regime=plan.regime,
            equity=equity,
            cash=portfolio.cash,
            entries=plan.entries,
            holdings=holdings_brief,
            pending_exits=[x.brief() for x in plan.exits],
        )

        try:
            result = advisor.review(brief)
        except Exception as e:  # noqa: BLE001 — the advisor is never required
            _log.warning(f'Advisor call raised ({e}); keeping the rules-based plan')
            result = AdvisorResult(
                provider=getattr(advisor, 'provider', config.provider),
                model=config.model,
                error=str(e)[:400],
            )
        rows = apply_verdicts(plan, result, config.mode, holdings_map)

        if not rows:
            # Still record the attempt, so a silent advisor is visible.
            session.add(LlmAdvice(
                run_id=run.id,
                provider=result.provider,
                model=result.model,
                mode=config.mode,
                latency_ms=result.latency_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                error=result.error,
            ))
            if result.error:
                _log.warning(
                    f'Advisor unusable ({result.error}); '
                    f'continuing on the deterministic plan'
                )
        for row in rows:
            session.add(LlmAdvice(
                run_id=run.id,
                provider=result.provider,
                model=result.model,
                mode=config.mode,
                latency_ms=result.latency_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                **row,
            ))
        session.flush()
        return rows

    # ----------------------------------------------------------------- #
    # Execution
    # ----------------------------------------------------------------- #

    def _apply_plan(
        self,
        session: Session,
        portfolio: Portfolio,
        run: ScanRun,
        plan: ActionPlan,
        prices: Dict[str, float],
        regime: str,
        advice_rows: List[dict],
    ) -> dict:
        closed: List[dict] = []
        opened: List[dict] = []
        moves: List[dict] = []

        for exit_action in plan.exits:
            position = exit_action.position
            trade = self.portfolios.sell(
                session, portfolio, position, exit_action.price,
                exit_action.reason, run.id,
            )
            if trade is None:
                continue
            payload = {
                'symbol': position.instrument.symbol,
                'asset_class': position.instrument.asset_class,
                'qty': position.qty,
                'avg_entry': position.avg_entry,
                'price': trade.price,
                'realized_pnl': position.realized_pnl,
                'r_multiple': position.r_multiple(trade.price),
                'reason': exit_action.reason,
            }
            closed.append(payload)
            self.portfolios.record_event(
                session, portfolio, EventType.POSITION_CLOSED, payload, run.id
            )

        for move in plan.stop_moves:
            move.position.stop_price = move.new_stop
            move.position.high_water = max(
                move.position.high_water,
                prices.get(move.position.instrument.symbol, move.position.high_water),
            )
            payload = {
                'symbol': move.position.instrument.symbol,
                'old_stop': move.old_stop,
                'new_stop': move.new_stop,
            }
            moves.append(payload)
            self.portfolios.record_event(
                session, portfolio, EventType.STOP_MOVED, payload, run.id
            )

        for entry in plan.entries:
            instrument = session.get(Instrument, entry.candidate.instrument_id)
            if instrument is None:
                continue
            position = self.portfolios.buy(
                session, portfolio, instrument,
                qty=entry.qty,
                price=entry.entry_price,
                stop_price=entry.stop_price,
                target_price=entry.target_price,
                r_value=entry.r_value,
                atr=entry.atr,
                max_hold_days=entry.max_hold_days,
                entry_score=entry.candidate.score,
                run_id=run.id,
            )
            if position is None:
                continue
            payload = {
                'symbol': instrument.symbol,
                'asset_class': instrument.asset_class,
                'qty': position.qty,
                'price': position.avg_entry,
                'stop_price': position.stop_price,
                'target_price': position.target_price,
                'score': entry.candidate.score,
                'flags': list(entry.candidate.flags),
                'advisor_note': entry.advisor_note,
            }
            opened.append(payload)
            self.portfolios.record_event(
                session, portfolio, EventType.POSITION_OPENED, payload, run.id
            )

        if plan.halted:
            self.portfolios.record_event(
                session, portfolio, EventType.RISK_HALT,
                {'reason': plan.halt_reason}, run.id,
            )

        run.opened = len(opened)
        run.closed = len(closed)

        positions = self.portfolios.open_positions(session, portfolio)
        snapshot = self.portfolios.snapshot(session, portfolio, prices, positions)

        return {
            'portfolio_id': portfolio.id,
            'portfolio_name': portfolio.name,
            'run_id': run.id,
            'sleeve': run.sleeve,
            'regime': regime,
            'halted': plan.halted,
            'halt_reason': plan.halt_reason,
            'opened': opened,
            'closed': closed,
            'stop_moves': moves,
            'advice': advice_rows,
            'advisor_mode': self._ctx.config.advisor.mode,
            'advisor_model': (
                self._ctx.config.advisor.model
                if self._ctx.config.advisor.enabled else ''
            ),
            'equity': snapshot.equity,
            'cash': portfolio.cash,
            'open_positions': len(positions),
            'total_return': _total_return(portfolio, snapshot.equity),
            'dry_run': False,
        }

    def _dry_run_summary(
        self,
        session: Session,
        portfolio: Portfolio,
        run: ScanRun,
        plan: ActionPlan,
        prices: Dict[str, float],
        open_positions: List[Position],
        regime: str,
        advice_rows: List[dict],
        candidates: Dict[str, Candidate],
    ) -> dict:
        equity = self.portfolios.equity(portfolio, open_positions, prices)
        ranked = scanner.rank(list(candidates.values()))[:20]
        return {
            'portfolio_id': portfolio.id,
            'portfolio_name': portfolio.name,
            'run_id': run.id,
            'sleeve': run.sleeve,
            'regime': regime,
            'halted': plan.halted,
            'halt_reason': plan.halt_reason,
            'would_open': [e.brief() for e in plan.entries],
            'would_close': [x.brief() for x in plan.exits],
            'would_move_stops': [
                {
                    'symbol': m.position.instrument.symbol,
                    'old_stop': m.old_stop,
                    'new_stop': m.new_stop,
                }
                for m in plan.stop_moves
            ],
            'top_candidates': [c.brief() for c in ranked],
            'advice': advice_rows,
            'equity': equity,
            'cash': portfolio.cash,
            'open_positions': len(open_positions),
            'total_return': _total_return(portfolio, equity),
            'dry_run': True,
        }

    def _record_signals(
        self,
        session: Session,
        run: ScanRun,
        candidates: Dict[str, Candidate],
        plan: ActionPlan,
    ) -> None:
        entering = {e.candidate.symbol for e in plan.entries}
        exiting = {x.position.instrument.symbol for x in plan.exits}
        for candidate in candidates.values():
            decision = ''
            if candidate.symbol in entering:
                decision = 'enter'
            elif candidate.symbol in exiting:
                decision = 'exit'
            session.add(Signal(
                run_id=run.id,
                instrument_id=candidate.instrument_id,
                score=candidate.score,
                price=candidate.price,
                gap_pct=candidate.gap_pct,
                rvol=candidate.rvol,
                atr_pct=candidate.atr_pct,
                rsi=candidate.rsi,
                adx=candidate.adx,
                mom_20=candidate.mom_20,
                mom_60=candidate.mom_60,
                trend_ok=candidate.trend_ok,
                decision=decision,
                flags=list(candidate.flags),
            ))
        session.flush()

    # ----------------------------------------------------------------- #
    # Notification
    # ----------------------------------------------------------------- #

    def _notify(self, summary: dict) -> None:
        if summary.get('skipped'):
            return
        with self.db.session() as session:
            portfolio = self.portfolios.get(session, summary['portfolio_id'])
            run = session.get(ScanRun, summary['run_id'])
            if portfolio is None or run is None:
                return
            if summary.get('halted'):
                self._ctx.notifier.notify_risk(
                    portfolio,
                    'Trading halted',
                    summary.get('halt_reason') or 'Risk limit reached',
                    summary,
                )
            self._ctx.notifier.notify_run(portfolio, run, summary)

    # ----------------------------------------------------------------- #
    # Digest
    # ----------------------------------------------------------------- #

    def send_digest(self, portfolio_id: int) -> bool:
        with self.db.session() as session:
            portfolio = self.portfolios.get(session, portfolio_id)
            if portfolio is None:
                return False

            positions = self.portfolios.open_positions(session, portfolio)
            instruments = [p.instrument for p in positions]
            prices = {
                symbol: quote.price
                for symbol, quote in self.market.get_quotes(
                    session, instruments
                ).items()
            }
            for position in positions:
                prices.setdefault(position.instrument.symbol, position.avg_entry)

            snapshot = self.portfolios.snapshot(
                session, portfolio, prices, positions
            )
            previous = self.portfolios.previous_snapshot(session, portfolio)
            baseline = previous.equity if previous else portfolio.initial_capital
            day_return = (
                (snapshot.equity - baseline) / baseline * 100 if baseline > 0 else 0.0
            )

            benchmark = self.benchmark_instrument(session)
            regime = strategy.detect_regime(
                self.market.get_bars(session, benchmark)
            ) if benchmark else Regime.NEUTRAL

            summary = {
                'as_of': datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC'),
                'regime': regime,
                'equity': snapshot.equity,
                'cash': portfolio.cash,
                'total_return': _total_return(portfolio, snapshot.equity),
                'day_return': day_return,
                'realized_pnl': snapshot.realized_pnl,
                'unrealized_pnl': snapshot.unrealized_pnl,
                'drawdown_pct': snapshot.drawdown_pct,
                'positions': [
                    {
                        'symbol': p.instrument.symbol,
                        'qty': p.qty,
                        'avg_entry': p.avg_entry,
                        'price': prices.get(p.instrument.symbol, p.avg_entry),
                        'stop_price': p.stop_price,
                        'unrealized': p.unrealized_pnl(
                            prices.get(p.instrument.symbol, p.avg_entry)
                        ),
                        'r_multiple': p.r_multiple(
                            prices.get(p.instrument.symbol, p.avg_entry)
                        ),
                        'held_days': _held_days(p),
                    }
                    for p in positions
                ],
            }
            return self._ctx.notifier.notify_digest(portfolio, summary)


def _total_return(portfolio: Portfolio, equity: float) -> float:
    if portfolio.initial_capital <= 0:
        return 0.0
    return (equity - portfolio.initial_capital) / portfolio.initial_capital * 100


def _held_days(position: Position) -> int:
    entered = position.entry_at
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - entered).days, 0)
