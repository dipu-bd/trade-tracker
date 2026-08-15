from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.broker.service import BrokerService
from tradebot.core.clock import Clock
from tradebot.db.models import (
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionStatus,
    Side,
)
from tradebot.obs import EventRecorder


@dataclass(frozen=True, slots=True)
class StopBreach:
    portfolio_id: int
    symbol: str
    price: Decimal
    stop_price: Decimal
    order_id: int


class StopTrigger:
    """Protective exits between scheduled cycles.

    A stop that only fires when the next cron happens to run is not a stop, so a breached level
    is acted on as the quote arrives. This is the only path that trades outside a decision
    cycle, and it can only ever reduce a position.
    """

    def __init__(self, broker: BrokerService, events: EventRecorder, clock: Clock) -> None:
        self._broker = broker
        self._events = events
        self._clock = clock

    async def on_quote(
        self, session: AsyncSession, symbol: str, price: Decimal
    ) -> list[StopBreach]:
        instrument = await session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        if instrument is None:
            return []

        positions = await session.scalars(
            select(Position).where(
                Position.instrument_id == instrument.id,
                Position.status == PositionStatus.OPEN,
                Position.stop_price.is_not(None),
                Position.stop_price >= price,
                Position.qty > 0,
            )
        )

        breaches: list[StopBreach] = []
        for position in positions:
            if await self._has_working_order(session, position):
                continue

            portfolio = await session.get(Portfolio, position.portfolio_id)
            if portfolio is None:
                continue

            stamp = int(self._clock.now().timestamp())
            order = await self._broker.place_order(
                session,
                portfolio=portfolio,
                instrument=instrument,
                side=Side.SELL,
                qty=position.qty,
                order_type=OrderType.MARKET,
                reference_price=price,
                client_order_id=f"stop-{position.id}-{stamp}",
            )
            breach = StopBreach(
                portfolio_id=portfolio.id,
                symbol=instrument.symbol,
                price=price,
                stop_price=position.stop_price or Decimal(0),
                order_id=order.id,
            )
            breaches.append(breach)

            await self._events.record(
                session,
                domain="risk",
                kind="stop_triggered",
                severity="warning",
                user_id=portfolio.user_id,
                portfolio_id=portfolio.id,
                message=f"{instrument.symbol} traded through its stop",
                payload={
                    "symbol": instrument.symbol,
                    "price": str(price),
                    "stop_price": str(breach.stop_price),
                },
            )

        return breaches

    async def _has_working_order(self, session: AsyncSession, position: Position) -> bool:
        existing = await session.scalar(
            select(Order.id).where(
                Order.portfolio_id == position.portfolio_id,
                Order.instrument_id == position.instrument_id,
                Order.status.in_(sorted(OrderStatus.OPEN)),
            )
        )
        return existing is not None
