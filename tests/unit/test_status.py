import pytest

from app.db.models import Email, EmailStatus
from app.db.status import ALLOWED_TRANSITIONS, IllegalTransitionError, transition


def test_every_status_has_an_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(EmailStatus)


def test_approved_is_only_reachable_from_draft_ready() -> None:
    sources = {
        status for status, targets in ALLOWED_TRANSITIONS.items() if EmailStatus.APPROVED in targets
    }
    assert sources == {EmailStatus.DRAFT_READY}


def test_illegal_transition_is_refused() -> None:
    email = Email(status=EmailStatus.RECEIVED)
    with pytest.raises(IllegalTransitionError):
        transition(email, EmailStatus.APPROVED)
    assert email.status is EmailStatus.RECEIVED


def test_legal_transition_changes_the_status() -> None:
    email = Email(status=EmailStatus.RECEIVED)
    transition(email, EmailStatus.PROCESSING)
    assert email.status is EmailStatus.PROCESSING
