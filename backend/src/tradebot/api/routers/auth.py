from fastapi import APIRouter, Request, Response, status

from tradebot.api.deps import REFRESH_COOKIE, Context, CurrentUser, DbSession
from tradebot.core.errors import AuthenticationError
from tradebot.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, context: Context, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=context.settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=context.settings.cookie_secure,
        samesite="lax",
        domain=context.settings.cookie_domain,
        path="/api/auth",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, context: Context, session: DbSession) -> UserOut:
    """Create an account. The first account registered becomes the admin."""
    user = await context.auth.register(
        session,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
    )
    await context.events.record(
        session, domain="auth", kind="user_registered", user_id=user.id, message=user.email
    )
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    context: Context,
    session: DbSession,
) -> TokenResponse:
    """Exchange credentials for a short-lived access token and a refresh cookie."""
    user = await context.auth.authenticate(session, email=body.email, password=body.password)
    access, refresh = await context.auth.issue_session(
        session,
        user=user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, context, refresh)
    await context.events.record(session, domain="auth", kind="login", user_id=user.id)
    return TokenResponse(access_token=access, expires_in=context.settings.access_token_ttl_seconds)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request, response: Response, context: Context, session: DbSession
) -> TokenResponse:
    """Rotate the refresh cookie and mint a new access token."""
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AuthenticationError("missing refresh token")

    _, access, new_refresh = await context.auth.rotate(session, refresh_token=token)
    _set_refresh_cookie(response, context, new_refresh)
    return TokenResponse(access_token=access, expires_in=context.settings.access_token_ttl_seconds)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, context: Context, session: DbSession
) -> None:
    """Revoke the current refresh session and clear its cookie."""
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        await context.auth.revoke(session, refresh_token=token)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth", domain=context.settings.cookie_domain)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    """The authenticated account."""
    return UserOut.model_validate(user)
