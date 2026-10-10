"""Delete personal data that is past its retention period (GR-2). Meant to run daily.

uv run python scripts/purge_expired.py
"""

import asyncio

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.privacy.retention import purge_expired


async def _main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        async with create_session_factory(engine)() as session:
            purged = await purge_expired(session, retention_days=settings.pii_retention_days)
            await session.commit()
    finally:
        await engine.dispose()
    print(f"Purged personal data of {purged} emails older than {settings.pii_retention_days} days")


if __name__ == "__main__":
    asyncio.run(_main())
