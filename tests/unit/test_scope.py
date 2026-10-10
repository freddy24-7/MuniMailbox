import uuid

import pytest

from app.auth.scope import can_access_email
from app.db.models import Email, Role, User


def _user(role: Role, team: str | None) -> User:
    return User(id=uuid.uuid4(), name="Test User", role=role, team=team)


@pytest.mark.parametrize(
    ("role", "user_team", "email_team", "allowed"),
    [
        (Role.CASEWORKER, "A", "A", True),
        (Role.CASEWORKER, "A", "B", False),
        (Role.CASEWORKER, "A", None, False),
        (Role.TEAM_LEAD, "A", "A", True),
        (Role.TEAM_LEAD, "A", "B", False),
        (Role.TEAM_LEAD, "A", None, True),  # unrouted emails are a team lead's to route
        (Role.AUDITOR, None, "A", False),
        (Role.AUDITOR, None, None, False),  # no team must never match "no team"
    ],
)
def test_team_scope(
    role: Role, user_team: str | None, email_team: str | None, allowed: bool
) -> None:
    assert can_access_email(_user(role, user_team), Email(team=email_team)) is allowed


def test_assignment_gives_a_caseworker_access_across_teams() -> None:
    caseworker = _user(Role.CASEWORKER, "A")
    assert can_access_email(caseworker, Email(team="B", assigned_to=caseworker.id))
    assert not can_access_email(caseworker, Email(team="B", assigned_to=uuid.uuid4()))
