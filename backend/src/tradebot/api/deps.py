from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.context import AppContext
from tradebot.core.errors import AuthenticationError
from tradebot.db.models import User

REFRESH_COOKIE = "tradebot_refresh"


def get_context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


Context = Annotated[AppContext, Depends(get_context)]


async def get_session(context: Context) -> AsyncIterator[AsyncSession]:
    async with context.db.session() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def current_user(request: Request, context: Context, session: DbSession) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("missing bearer token")
    return await context.auth.user_from_access_token(session, token=token)


CurrentUser = Annotated[User, Depends(current_user)]
