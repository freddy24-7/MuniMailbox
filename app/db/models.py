"""Relational data model (PRD "Data model").

Where personal data lives, and in what form:
- `Citizen`: clear text. It stands in for the municipality's own case-management system.
- `Email.original_encrypted`: the original sender, subject and body, encrypted.
- `PiiMapping.encrypted_value`: the value behind each placeholder, encrypted.
- `OutboxMessage.payload_encrypted`: the approved reply with values restored, encrypted.
- Every column ending in `_redacted`: placeholders only. This is what models and auditors see.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.privacy.entities import EntityType


class Topic(StrEnum):
    PERMITS = "permits"
    WASTE_COLLECTION = "waste_collection"
    BENEFITS = "benefits"
    COMPLAINTS = "complaints"
    ADDRESS_CHANGE = "address_change"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class EmailStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    DRAFT_READY = "draft_ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_ATTENTION = "needs_attention"


class CaseStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class Role(StrEnum):
    CASEWORKER = "caseworker"
    TEAM_LEAD = "team_lead"
    AUDITOR = "auditor"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    REASSIGN = "reassign"


def _enum(enum_class: type[StrEnum]) -> Enum:
    # Stored as a plain string with the enum *value*, so adding a member needs no migration.
    return Enum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(_enum(Role))
    team: Mapped[str | None] = mapped_column(String(100))


class Citizen(Base):
    __tablename__ = "citizens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300))
    bsn: Mapped[str] = mapped_column(String(9), unique=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    phone: Mapped[str] = mapped_column(String(32))


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    citizen_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("citizens.id"), index=True)
    topic: Mapped[Topic] = mapped_column(_enum(Topic))
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text)


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Carried by every event and log line about this email (NFR-2).
    correlation_id: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4)
    citizen_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("citizens.id"), index=True)
    original_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    # Null until the privacy agent has run.
    subject_redacted: Mapped[str | None] = mapped_column(Text)
    body_redacted: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Null until the intake agent has run.
    topic: Mapped[Topic | None] = mapped_column(_enum(Topic))
    urgency: Mapped[Urgency | None] = mapped_column(_enum(Urgency))
    status: Mapped[EmailStatus] = mapped_column(
        _enum(EmailStatus), default=EmailStatus.RECEIVED, index=True
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)


class PiiMapping(Base):
    __tablename__ = "pii_mappings"
    __table_args__ = (UniqueConstraint("email_id", "placeholder"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("emails.id"), index=True)
    placeholder: Mapped[str] = mapped_column(String(64))
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary)
    entity_type: Mapped[EntityType] = mapped_column(_enum(EntityType))
    # The retention job deletes mappings by age (GR-2).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RedactionRecord(Base):
    """Proof that the privacy agent processed an email. The inference router refuses
    any model call for an email without one (FR-14). Contains counts, never values."""

    __tablename__ = "redaction_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("emails.id"), unique=True)
    entity_counts: Mapped[dict[str, int]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("emails.id"), index=True)
    text_redacted: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model_used: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drafts.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[ReviewAction] = mapped_column(_enum(ReviewAction))
    # The caseworker edits restored text; it is redacted again before it is stored.
    edited_text_redacted: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxMessage(Base):
    """A reply that has been "sent" (FR-21, simulated).

    `review_id` is required and unique: the schema itself does not allow an outbox row
    without the review that approved it.
    """

    __tablename__ = "outbox_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("emails.id"), index=True)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id"), unique=True)
    # Recipient, subject and body with personal data restored, encrypted.
    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    """Append-only and hash-chained (GR-7, GR-8). Never updated or deleted."""

    __tablename__ = "audit_events"

    # Sequential, because the chain needs one total order.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    email_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("emails.id"), index=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    left_local: Mapped[bool] = mapped_column(default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Unique: two records can never claim the same predecessor, so the chain cannot fork.
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
