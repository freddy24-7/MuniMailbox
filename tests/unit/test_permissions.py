import pytest

from app.auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    PermissionDeniedError,
    has_permission,
    require_permission,
)
from app.db.models import Role


def test_every_role_is_defined() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_only_caseworkers_can_approve() -> None:
    allowed = {role for role in Role if has_permission(role, Permission.DRAFT_APPROVE)}
    assert allowed == {Role.CASEWORKER}


def test_auditors_can_never_see_restored_data() -> None:
    assert not has_permission(Role.AUDITOR, Permission.EMAIL_VIEW_RESTORED)
    with pytest.raises(PermissionDeniedError):
        require_permission(Role.AUDITOR, Permission.EMAIL_VIEW_RESTORED)
