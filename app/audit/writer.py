import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from app.db.models import AuditEvent

GENESIS_HASH = "0" * 64
# Arbitrary constant naming the PostgreSQL advisory lock that serialises chain appends.
_CHAIN_LOCK_ID = 7_041_776


class AuditImmutableError(Exception):
    pass


def compute_hash(
    *,
    prev_hash: str,
    email_id: uuid.UUID | None,
    actor: str,
    action: str,
    model: str | None,
    left_local: bool,
    timestamp: datetime,
) -> str:
    """SHA-256 over a canonical form of the record plus the previous record's hash.

    Canonical means: the same record always becomes the same bytes (sorted keys, fixed
    separators, UTC timestamps), or verification would fail for the wrong reasons.
    """
    if timestamp.tzinfo is None:  # SQLite returns naive datetimes; they were stored as UTC
        timestamp = timestamp.replace(tzinfo=UTC)
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "email_id": str(email_id) if email_id else None,
            "actor": actor,
            "action": action,
            "model": model,
            "left_local": left_local,
            "timestamp": timestamp.astimezone(UTC).isoformat(timespec="microseconds"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    email_id: uuid.UUID | None = None,
    model: str | None = None,
    left_local: bool = False,
) -> AuditEvent:
    """Append one event to the chain (GR-7, GR-8). The caller commits, so the event and
    the change it describes are saved together or not at all."""
    if session.get_bind().dialect.name == "postgresql":
        # Two concurrent appends must not read the same "last" record. Held until commit.
        await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _CHAIN_LOCK_ID})

    prev_hash = (
        await session.scalar(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1))
    ) or GENESIS_HASH
    timestamp = datetime.now(UTC)
    audit_event = AuditEvent(
        email_id=email_id,
        actor=actor,
        action=action,
        model=model,
        left_local=left_local,
        timestamp=timestamp,
        prev_hash=prev_hash,
        hash=compute_hash(
            prev_hash=prev_hash,
            email_id=email_id,
            actor=actor,
            action=action,
            model=model,
            left_local=left_local,
            timestamp=timestamp,
        ),
    )
    session.add(audit_event)
    await session.flush()
    return audit_event


@dataclass(frozen=True)
class ChainStatus:
    ok: bool
    checked: int
    first_broken_id: int | None = None


async def verify_chain(session: AsyncSession) -> ChainStatus:
    """Walk the chain from the start and report the first record that does not fit."""
    expected_prev = GENESIS_HASH
    checked = 0
    events = await session.stream_scalars(select(AuditEvent).order_by(AuditEvent.id))
    async for audit_event in events:
        recomputed = compute_hash(
            prev_hash=audit_event.prev_hash,
            email_id=audit_event.email_id,
            actor=audit_event.actor,
            action=audit_event.action,
            model=audit_event.model,
            left_local=audit_event.left_local,
            timestamp=audit_event.timestamp,
        )
        if audit_event.prev_hash != expected_prev or audit_event.hash != recomputed:
            return ChainStatus(ok=False, checked=checked, first_broken_id=audit_event.id)
        expected_prev = audit_event.hash
        checked += 1
    return ChainStatus(ok=True, checked=checked)


# Append-only at the application level: the ORM refuses to change or remove audit rows.
# Someone with direct database access can still do it; the hash chain is what reveals that.
@event.listens_for(AuditEvent, "before_update")
def _refuse_update(*_: object) -> None:
    raise AuditImmutableError("audit events cannot be updated")


@event.listens_for(AuditEvent, "before_delete")
def _refuse_delete(*_: object) -> None:
    raise AuditImmutableError("audit events cannot be deleted")


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_change(state: ORMExecuteState) -> None:
    if (state.is_update or state.is_delete) and any(
        mapper.class_ is AuditEvent for mapper in state.all_mappers
    ):
        raise AuditImmutableError("audit events cannot be updated or deleted")
