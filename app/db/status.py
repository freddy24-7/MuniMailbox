from app.db.models import Email, EmailStatus

# The email workflow as a state machine (FR-4). Anything not listed is a bug.
ALLOWED_TRANSITIONS: dict[EmailStatus, frozenset[EmailStatus]] = {
    EmailStatus.RECEIVED: frozenset({EmailStatus.PROCESSING, EmailStatus.NEEDS_ATTENTION}),
    EmailStatus.PROCESSING: frozenset({EmailStatus.DRAFT_READY, EmailStatus.NEEDS_ATTENTION}),
    EmailStatus.DRAFT_READY: frozenset({EmailStatus.APPROVED, EmailStatus.REJECTED}),
    EmailStatus.NEEDS_ATTENTION: frozenset({EmailStatus.PROCESSING}),
    EmailStatus.APPROVED: frozenset(),
    EmailStatus.REJECTED: frozenset(),
}


class IllegalTransitionError(Exception):
    pass


def transition(email: Email, new_status: EmailStatus) -> None:
    if new_status not in ALLOWED_TRANSITIONS[email.status]:
        raise IllegalTransitionError(f"{email.status} -> {new_status}")
    email.status = new_status
