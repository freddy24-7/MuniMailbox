import re
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission, require_permission
from app.auth.scope import require_email_access
from app.db.models import Email, PiiMapping, User
from app.privacy.crypto import Cipher
from app.privacy.original import OriginalEmail

_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")


def _require_may_see(user: User, email: Email) -> None:
    require_permission(user.role, Permission.EMAIL_VIEW_RESTORED)
    require_email_access(user, email)


async def restore(
    session: AsyncSession, cipher: Cipher, *, user: User, email: Email, texts: Sequence[str]
) -> list[str]:
    """Put the original values back for display (FR-12).

    The checks live here, not in the caller, so there is no way to get restored text
    without them (GR-3, GR-6). Raises PermissionDeniedError for a role that may not see
    personal data, and for an email outside the user's team.

    After the retention period the mapping is gone and placeholders stay as they are.
    """
    _require_may_see(user, email)
    rows = await session.execute(
        select(PiiMapping.placeholder, PiiMapping.encrypted_value).where(
            PiiMapping.email_id == email.id
        )
    )
    mapping = {placeholder: cipher.decrypt(value) for placeholder, value in rows}
    # A placeholder the model invented has no mapping and is left as it is.
    return [_PLACEHOLDER.sub(lambda m: mapping.get(m.group(), m.group()), text) for text in texts]


def read_original(cipher: Cipher, *, user: User, email: Email) -> OriginalEmail | None:
    """The email as the citizen wrote it, or None once retention has deleted it.
    Same rule as `restore`."""
    _require_may_see(user, email)
    if email.original_encrypted is None:
        return None
    return OriginalEmail.decrypt(cipher, email.original_encrypted)
