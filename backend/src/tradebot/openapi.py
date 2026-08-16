import json
import secrets
from pathlib import Path

from tradebot.core.settings import Settings
from tradebot.main import create_app

OUTPUT = Path(__file__).resolve().parents[2] / "openapi.json"


def main() -> None:
    """Dump the schema the frontend client is generated from.

    Written by a script rather than committed by hand so the TypeScript client can never drift
    from the routes the app actually serves.
    """
    settings = Settings(
        env="test",
        database_url="sqlite+aiosqlite:///./schema.db",
        secret_key=secrets.token_urlsafe(32),
        scheduler_enabled=False,
    )
    OUTPUT.write_text(json.dumps(create_app(settings).openapi(), indent=2) + "\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
