import argparse
import asyncio
import os
import sys

from sqlalchemy import select

from tradebot.context import AppContext
from tradebot.core.settings import get_settings
from tradebot.db.models import User
from tradebot.providers.base import AssetClass, Capability


async def _first_user(context: AppContext) -> User | None:
    async with context.db.session() as session:
        found: User | None = await session.scalar(select(User).order_by(User.id))
        return found


async def seed(email: str | None) -> int:
    """Move provider keys from the environment into the vault for one account."""
    context = AppContext.build(get_settings())
    try:
        async with context.db.session() as session:
            stmt = select(User).order_by(User.id)
            if email:
                stmt = stmt.where(User.email == email.lower())
            user = await session.scalar(stmt)

            if user is None:
                print("no account found; register one first", file=sys.stderr)
                return 1

            seeded = await context.providers.seed_from_env(session, user.id, dict(os.environ))
            summary = await context.providers.masked_summary(session, user.id)

        print(f"account: {user.email}")
        print(f"seeded : {', '.join(seeded) if seeded else 'nothing new'}")
        for row in summary:
            stored = row["fields"] if isinstance(row["fields"], list) else []
            fields = ", ".join(f"{f['field']}={f['masked']}" for f in stored)
            state = "keyless" if row["keyless"] else (fields or "not configured")
            print(f"  {row['provider']:14} {state}")
        return 0
    finally:
        await context.aclose()


async def check() -> int:
    """Exercise every configured provider against its live endpoint."""
    context = AppContext.build(get_settings())
    failures = 0
    try:
        user = await _first_user(context)
        if user is None:
            print("no account found; register one first", file=sys.stderr)
            return 1

        async with context.db.session() as session:
            router = await context.providers.build_router(session, user.id)

        for capability, asset_class in (
            (Capability.UNIVERSE, AssetClass.CRYPTO),
            (Capability.UNIVERSE, AssetClass.STOCK),
            (Capability.UNIVERSE, AssetClass.ETF),
            (Capability.QUOTES, AssetClass.CRYPTO),
            (Capability.QUOTES, AssetClass.STOCK),
            (Capability.BARS, AssetClass.CRYPTO),
            (Capability.BARS, AssetClass.STOCK),
            (Capability.NEWS, AssetClass.STOCK),
            (Capability.CORPORATE_ACTIONS, AssetClass.STOCK),
            (Capability.STREAM, AssetClass.CRYPTO),
            (Capability.STREAM, AssetClass.STOCK),
        ):
            chain = [p.key for p in router.candidates(capability, asset_class)]
            mark = "ok " if chain else "GAP"
            if not chain:
                failures += 1
            print(f"  {mark} {capability.value:18} {asset_class.value:9} {chain}")

        for provider in router.providers:
            if not provider.available:
                print(f"  -- {provider.key:14} unavailable: {list(provider.missing_credentials)}")

        return 1 if failures else 0
    finally:
        await context.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradebot")
    sub = parser.add_subparsers(dest="command", required=True)

    seed_parser = sub.add_parser("seed", help="load provider keys from the environment")
    seed_parser.add_argument("--email", default=None)
    sub.add_parser("check", help="report provider coverage per capability")

    args = parser.parse_args(argv)
    if args.command == "seed":
        return asyncio.run(seed(args.email))
    return asyncio.run(check())


if __name__ == "__main__":
    raise SystemExit(main())
