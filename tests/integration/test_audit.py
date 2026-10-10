import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.audit.writer import GENESIS_HASH, AuditImmutableError, verify_chain
from app.db.models import AuditEvent


async def _three_events(session: AsyncSession) -> list[AuditEvent]:
    events = [
        await audit.record(session, actor="agent:privacy", action=f"step.{number}")
        for number in range(3)
    ]
    await session.commit()
    return events


async def _tamper(session: AsyncSession, statement: str, **parameters: object) -> None:
    """Change the table the way an attacker with full database rights would: on
    PostgreSQL that means switching the append-only trigger off first."""
    is_postgres = session.get_bind().dialect.name == "postgresql"
    if is_postgres:
        await session.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
    await session.execute(text(statement), parameters)
    if is_postgres:
        await session.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
    await session.commit()
    session.expire_all()


async def test_each_record_points_at_the_previous_one(session: AsyncSession) -> None:
    first, second, third = await _three_events(session)
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash
    assert await verify_chain(session) == audit.ChainStatus(ok=True, checked=3)


async def test_changing_a_record_behind_the_orm_is_detected(session: AsyncSession) -> None:
    """Acceptance criterion: tampering with an audit record is detected."""
    _, second, _ = await _three_events(session)
    await _tamper(
        session, "UPDATE audit_events SET action = 'nothing.happened' WHERE id = :id", id=second.id
    )

    status = await verify_chain(session)
    assert not status.ok
    assert status.first_broken_id == second.id


async def test_removing_a_record_behind_the_orm_is_detected(session: AsyncSession) -> None:
    _, second, third = await _three_events(session)
    await _tamper(session, "DELETE FROM audit_events WHERE id = :id", id=second.id)

    status = await verify_chain(session)
    assert not status.ok
    assert status.first_broken_id == third.id


async def test_the_orm_refuses_to_update_or_delete(session: AsyncSession) -> None:
    first, second, _ = await _three_events(session)

    first.action = "rewritten"
    with pytest.raises(AuditImmutableError):
        await session.flush()
    await session.rollback()

    await session.delete(second)
    with pytest.raises(AuditImmutableError):
        await session.flush()
    await session.rollback()

    with pytest.raises(AuditImmutableError):
        await session.execute(update(AuditEvent).values(action="rewritten"))
    with pytest.raises(AuditImmutableError):
        await session.execute(delete(AuditEvent))

    assert (await verify_chain(session)).ok


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'nothing.happened'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_the_database_itself_refuses_changes(session: AsyncSession, statement: str) -> None:
    """GR-8 below the application: raw SQL that never passes through the ORM."""
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("the append-only trigger exists on PostgreSQL only")
    await _three_events(session)

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text(statement))
    await session.rollback()

    assert await verify_chain(session) == audit.ChainStatus(ok=True, checked=3)
