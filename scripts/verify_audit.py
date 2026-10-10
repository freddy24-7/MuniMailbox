"""Verify the audit hash chain (GR-8). Exits non-zero if a record was changed.

uv run python scripts/verify_audit.py
"""

import asyncio
import sys

from app.audit.writer import verify_chain
from app.config import get_settings
from app.db.session import create_engine, create_session_factory


async def _main() -> int:
    engine = create_engine(get_settings().database_url.get_secret_value())
    try:
        async with create_session_factory(engine)() as session:
            status = await verify_chain(session)
    finally:
        await engine.dispose()
    if status.ok:
        print(f"OK: {status.checked} audit events, chain intact")
        return 0
    print(
        f"BROKEN at audit event id {status.first_broken_id} "
        f"({status.checked} events before it verified)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
