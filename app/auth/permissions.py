"""The single place where roles and permissions are defined (GR-5).

Endpoints and services ask for a permission, never for a role name.
"""

from enum import StrEnum

from app.db.models import Role


class Permission(StrEnum):
    QUEUE_VIEW = "queue:view"
    INTAKE_PULL = "intake:pull"
    # See an email and its draft with personal data restored (GR-3).
    EMAIL_VIEW_RESTORED = "email:view_restored"
    # Move a draft to the outbox. Caseworkers only (CLAUDE.md rule 3).
    DRAFT_APPROVE = "draft:approve"
    AUDIT_VIEW = "audit:view"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.CASEWORKER: frozenset(
        {
            Permission.QUEUE_VIEW,
            Permission.INTAKE_PULL,
            Permission.EMAIL_VIEW_RESTORED,
            Permission.DRAFT_APPROVE,
        }
    ),
    Role.TEAM_LEAD: frozenset(
        {Permission.QUEUE_VIEW, Permission.INTAKE_PULL, Permission.EMAIL_VIEW_RESTORED}
    ),
    # Auditors see the process, never the person.
    Role.AUDITOR: frozenset({Permission.AUDIT_VIEW}),
}


class PermissionDeniedError(Exception):
    pass


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def require_permission(role: Role, permission: Permission) -> None:
    if not has_permission(role, permission):
        raise PermissionDeniedError(permission.value)
