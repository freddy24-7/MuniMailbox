import re
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission, require_permission
from app.db.models import Email, PiiMapping, User
from app.privacy.crypto import Cipher
from app.privacy.original import OriginalEmail

_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")


async def restore(
    session: AsyncSession, cipher: Cipher, *, user: User, email_id: uuid.UUID, texts: Sequence[str]
) -> list[str]:
    """Put the original values back for display (FR-12).

    The role check lives here, not in the caller, so there is no way to get restored
    text without it (GR-3). Raises PermissionDeniedError for roles such as auditor.
    """
    require_permission(user.role, Permission.EMAIL_VIEW_RESTORED)
    rows = await session.execute(
        select(PiiMapping.placeholder, PiiMapping.encrypted_value).where(
            PiiMapping.email_id == email_id
        )
    )
    mapping = {placeholder: cipher.decrypt(value) for placeholder, value in rows}
    # A placeholder the model invented has no mapping and is left as it is.
    return [_PLACEHOLDER.sub(lambda m: mapping.get(m.group(), m.group()), text) for text in texts]


def read_original(cipher: Cipher, *, user: User, email: Email) -> OriginalEmail:
    """The email as the citizen wrote it. Same rule as `restore`."""
    require_permission(user.role, Permission.EMAIL_VIEW_RESTORED)
    return OriginalEmail.decrypt(cipher, email.original_encrypted)
