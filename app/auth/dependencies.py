from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission, has_permission
from app.auth.session import SESSION_COOKIE
from app.db.models import User
from app.db.session import get_session


class NotAuthenticatedError(Exception):
    """Handled in app.main: the browser is sent to the login page."""


async def current_user(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = request.app.state.services.session_codec.decode(token) if token else None
    user = await session.get(User, user_id) if user_id else None
    if user is None:
        raise NotAuthenticatedError
    return user


def require(permission: Permission) -> Callable[[User], Awaitable[User]]:
    """Dependency factory: `user: Annotated[User, Depends(require(Permission.X))]`.
    Every endpoint except login and health declares one."""

    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not allowed for your role")
        return user

    return dependency
