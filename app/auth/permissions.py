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
    # Change the text of a draft, or turn it down with a reason (FR-20).
    DRAFT_EDIT = "draft:edit"
    DRAFT_REJECT = "draft:reject"
    # Hand an email to another team or caseworker.
    EMAIL_REASSIGN = "email:reassign"
    AUDIT_VIEW = "audit:view"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.CASEWORKER: frozenset(
        {
            Permission.QUEUE_VIEW,
            Permission.INTAKE_PULL,
            Permission.EMAIL_VIEW_RESTORED,
            Permission.DRAFT_APPROVE,
            Permission.DRAFT_EDIT,
            Permission.DRAFT_REJECT,
            Permission.EMAIL_REASSIGN,
        }
    ),
    # A team lead assigns work but does not write or send replies.
    Role.TEAM_LEAD: frozenset(
        {
            Permission.QUEUE_VIEW,
            Permission.INTAKE_PULL,
            Permission.EMAIL_VIEW_RESTORED,
            Permission.EMAIL_REASSIGN,
        }
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
