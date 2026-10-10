"""Which emails a user may see (GR-6). The one place this rule is written.

Role permissions answer "may this user approve drafts at all?". This module answers the
second question every time: "may this user touch *this* email?".
"""

from sqlalchemy import ColumnElement, false, or_

from app.auth.permissions import PermissionDeniedError
from app.db.models import Email, Role, User


def can_access_email(user: User, email: Email) -> bool:
    in_own_team = user.team is not None and email.team == user.team
    if user.role is Role.CASEWORKER:
        return in_own_team or email.assigned_to == user.id
    if user.role is Role.TEAM_LEAD:
        # Emails no team has picked up yet are a team lead's to route.
        return in_own_team or email.team is None
    return False


def visible_emails(user: User) -> ColumnElement[bool]:
    """The same rule as a query filter, for lists."""
    in_own_team = Email.team == user.team if user.team is not None else false()
    if user.role is Role.CASEWORKER:
        return or_(in_own_team, Email.assigned_to == user.id)
    if user.role is Role.TEAM_LEAD:
        return or_(in_own_team, Email.team.is_(None))
    return false()


def require_email_access(user: User, email: Email) -> None:
    if not can_access_email(user, email):
        raise PermissionDeniedError("email outside the user's scope")
