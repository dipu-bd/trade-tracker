from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from tradebot.context import AppContext
from tradebot.db.models import Session, User


async def test_timestamps_read_back_timezone_aware(context: AppContext) -> None:
    """SQLite has no timestamptz; without the UtcDateTime decorator these come back naive."""
    async with context.db.session() as session:
        session.add(User(email="tz@example.com", password_hash="x", display_name="TZ"))

    async with context.db.session() as session:
        user = await session.scalar(select(User).where(User.email == "tz@example.com"))

    assert user is not None
    assert user.created_at.tzinfo is not None
    assert user.created_at <= datetime.now(UTC)


async def test_nullable_timestamps_survive_the_round_trip(context: AppContext) -> None:
    expires = datetime.now(UTC) + timedelta(days=1)
    async with context.db.session() as session:
        user = User(email="s@example.com", password_hash="x", display_name="S")
        session.add(user)
        await session.flush()
        session.add(Session(user_id=user.id, token_hash="hash", expires_at=expires))

    async with context.db.session() as session:
        record = await session.scalar(select(Session).where(Session.token_hash == "hash"))

    assert record is not None
    assert record.revoked_at is None
    assert record.expires_at.tzinfo is not None
    assert abs((record.expires_at - expires).total_seconds()) < 1


async def test_writing_a_naive_datetime_is_rejected(context: AppContext) -> None:
    async with context.db.session() as session:
        user = User(email="n@example.com", password_hash="x", display_name="N")
        session.add(user)
        await session.flush()
        user_id = user.id

    with pytest.raises(StatementError, match="naive datetime"):
        async with context.db.session() as session:
            session.add(
                Session(
                    user_id=user_id,
                    token_hash="naive",
                    expires_at=datetime(2030, 1, 1),
                )
            )
