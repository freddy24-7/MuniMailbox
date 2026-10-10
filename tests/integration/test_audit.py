import pytest
from sqlalchemy import delete, text, update
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


async def test_each_record_points_at_the_previous_one(session: AsyncSession) -> None:
    first, second, third = await _three_events(session)
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash
    assert await verify_chain(session) == audit.ChainStatus(ok=True, checked=3)


async def test_changing_a_record_behind_the_orm_is_detected(session: AsyncSession) -> None:
    """Acceptance criterion: tampering with an audit record is detected."""
    _, second, _ = await _three_events(session)
    await session.execute(
        text("UPDATE audit_events SET action = 'nothing.happened' WHERE id = :id"),
        {"id": second.id},
    )
    await session.commit()
    session.expire_all()

    status = await verify_chain(session)
    assert not status.ok
    assert status.first_broken_id == second.id


async def test_removing_a_record_behind_the_orm_is_detected(session: AsyncSession) -> None:
    _, second, third = await _three_events(session)
    await session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": second.id})
    await session.commit()
    session.expire_all()

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
