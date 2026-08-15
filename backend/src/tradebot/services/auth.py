from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.core import security
from tradebot.core.errors import AuthenticationError, ConflictError
from tradebot.core.settings import Settings
from tradebot.db.models import Session, User


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def register(
        self, session: AsyncSession, *, email: str, password: str, display_name: str
    ) -> User:
        email = email.strip().lower()
        existing = await session.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise ConflictError("an account with that email already exists")

        is_first = (await session.scalar(select(User.id).limit(1))) is None
        user = User(
            email=email,
            password_hash=security.hash_password(password),
            display_name=display_name.strip(),
            is_admin=is_first,
        )
        session.add(user)
        await session.flush()
        return user

    async def authenticate(self, session: AsyncSession, *, email: str, password: str) -> User:
        user = await session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            # Hash anyway so a missing account and a wrong password take the same time.
            security.hash_password(password)
            raise AuthenticationError("invalid email or password")
        if not security.verify_password(password, user.password_hash):
            raise AuthenticationError("invalid email or password")
        if not user.is_active:
            raise AuthenticationError("account is disabled")

        if security.needs_rehash(user.password_hash):
            user.password_hash = security.hash_password(password)
        user.last_login_at = datetime.now(UTC)
        return user

    async def issue_session(
        self,
        session: AsyncSession,
        *,
        user: User,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[str, str]:
        refresh = security.new_refresh_token()
        session.add(
            Session(
                user_id=user.id,
                token_hash=security.hash_refresh_token(refresh),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.refresh_token_ttl_seconds),
                user_agent=(user_agent or "")[:255] or None,
                ip_address=(ip_address or "")[:64] or None,
            )
        )
        access = security.create_access_token(
            user_id=user.id,
            secret=self._settings.secret_key,
            ttl_seconds=self._settings.access_token_ttl_seconds,
        )
        return access, refresh

    async def rotate(self, session: AsyncSession, *, refresh_token: str) -> tuple[User, str, str]:
        record = await session.scalar(
            select(Session).where(Session.token_hash == security.hash_refresh_token(refresh_token))
        )
        if record is None or record.revoked_at is not None:
            raise AuthenticationError("invalid refresh token")
        if record.expires_at <= datetime.now(UTC):
            raise AuthenticationError("refresh token expired")

        user = await session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("account is unavailable")

        record.revoked_at = datetime.now(UTC)
        access, refresh = await self.issue_session(
            session, user=user, user_agent=record.user_agent, ip_address=record.ip_address
        )
        return user, access, refresh

    async def revoke(self, session: AsyncSession, *, refresh_token: str) -> None:
        record = await session.scalar(
            select(Session).where(Session.token_hash == security.hash_refresh_token(refresh_token))
        )
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)

    async def user_from_access_token(self, session: AsyncSession, *, token: str) -> User:
        payload = security.decode_access_token(token, secret=self._settings.secret_key)
        user = await session.get(User, int(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthenticationError("account is unavailable")
        return user
