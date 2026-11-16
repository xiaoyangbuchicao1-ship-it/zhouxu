"""Small account application service backed by the relational adapter.

The service owns account rules and token lifecycle.  The API layer only maps
these outcomes to HTTP; it never hashes passwords or queries account tables.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import AccountModel, SessionModel

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AccountError(Exception):
    """An expected account outcome represented by a stable error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AccountPrincipal:
    id: str
    email: str
    display_name: str
    role: str = "user"
    status: str = "active"
    quota_tokens: int = 100_000
    used_tokens: int = 0

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.quota_tokens - self.used_tokens)


@dataclass(frozen=True)
class AuthResult:
    account: AccountPrincipal
    token: str


class AccountService:
    def __init__(
        self,
        database: Database,
        database_settings: DatabaseSettings,
        auth_settings: AuthSettings,
    ):
        self.database = database
        self.database_settings = database_settings
        self.auth_settings = auth_settings
        self.passwords = PasswordHasher()

    def register(self, *, email: str, password: str, display_name: str = "") -> AuthResult:
        normalized_email = self._normalize_email(email)
        self._validate_password(password)
        normalized_name = display_name.strip()
        if len(normalized_name) > 80:
            raise AccountError("display_name_too_long")
        now = self._now()
        account = AccountModel(
            id=secrets.token_hex(16),
            email=normalized_email,
            display_name=normalized_name,
            password_hash=self.passwords.hash(password),
            status="active",
            role="admin" if normalized_email in self._configured_admin_emails() else "user",
            quota_tokens=self.auth_settings.default_quota_tokens,
            created_at=now,
            updated_at=now,
        )
        session = self.database.session()
        try:
            session.add(account)
            session.flush()
        except IntegrityError:
            session.rollback()
            raise AccountError("account_email_exists") from None
        result = self._create_session(session, account, now)
        session.commit()
        session.close()
        return result

    def login(self, *, email: str, password: str) -> AuthResult:
        normalized_email = self._normalize_email(email)
        session = self.database.session()
        try:
            account = session.scalar(
                select(AccountModel).where(AccountModel.email == normalized_email)
            )
            if account is None or account.status != "active":
                raise AccountError("authentication_failed")
            try:
                valid = self.passwords.verify(account.password_hash, password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                valid = False
            if not valid:
                raise AccountError("authentication_failed")
            now = self._now()
            result = self._create_session(session, account, now)
            session.commit()
            return result
        finally:
            session.close()

    def authenticate(self, token: str) -> AccountPrincipal:
        if not token or len(token) > 256:
            raise AccountError("authentication_required")
        token_hash = self._hash_token(token)
        session = self.database.session()
        try:
            auth_session = session.scalar(
                select(SessionModel).where(SessionModel.token_hash == token_hash)
            )
            now = self._now()
            if (
                auth_session is None
                or auth_session.revoked_at is not None
                or self._as_utc(auth_session.expires_at) <= now
                or auth_session.account.status != "active"
            ):
                raise AccountError("authentication_required")
            auth_session.last_seen_at = now
            session.commit()
            return self._principal(auth_session.account)
        finally:
            session.close()

    def logout(self, token: str) -> None:
        if not token:
            raise AccountError("authentication_required")
        session = self.database.session()
        try:
            auth_session = session.scalar(
                select(SessionModel).where(SessionModel.token_hash == self._hash_token(token))
            )
            if auth_session is None or auth_session.revoked_at is not None:
                raise AccountError("authentication_required")
            auth_session.revoked_at = self._now()
            session.commit()
        finally:
            session.close()

    def is_admin(self, principal: AccountPrincipal) -> bool:
        """Return whether a principal may use the administrative API."""

        return principal.role == "admin" or principal.email in self._configured_admin_emails()

    def list_accounts(
        self, *, search: str = "", status: str | None = None,
        offset: int = 0, limit: int = 50,
    ) -> tuple[list[AccountPrincipal], int]:
        """List account metadata for the admin console without password data."""

        session = self.database.session()
        try:
            query = select(AccountModel)
            count_query = select(func.count()).select_from(AccountModel)
            value = search.strip().lower()
            if value:
                condition = AccountModel.email.contains(value) | AccountModel.display_name.contains(value)
                query = query.where(condition)
                count_query = count_query.where(condition)
            if status:
                query = query.where(AccountModel.status == status)
                count_query = count_query.where(AccountModel.status == status)
            total = int(session.scalar(count_query) or 0)
            accounts = session.scalars(
                query.order_by(AccountModel.created_at.desc()).offset(offset).limit(limit)
            ).all()
            return [self._principal(account) for account in accounts], total
        finally:
            session.close()

    def update_account(
        self, *, account_id: str, status: str | None = None,
        quota_tokens: int | None = None, used_tokens: int | None = None,
        role: str | None = None,
    ) -> AccountPrincipal:
        """Apply admin-managed account controls and return the new projection."""

        if status is not None and status not in {"active", "suspended"}:
            raise AccountError("account_status_invalid")
        if role is not None and role not in {"user", "admin"}:
            raise AccountError("account_role_invalid")
        if quota_tokens is not None and quota_tokens < 0:
            raise AccountError("quota_invalid")
        if used_tokens is not None and used_tokens < 0:
            raise AccountError("usage_invalid")
        session = self.database.session()
        try:
            account = session.get(AccountModel, account_id)
            if account is None:
                raise AccountError("account_not_found")
            next_used = account.used_tokens if used_tokens is None else used_tokens
            next_quota = account.quota_tokens if quota_tokens is None else quota_tokens
            if next_quota < next_used:
                raise AccountError("quota_below_usage")
            if status is not None:
                account.status = status
            if quota_tokens is not None:
                account.quota_tokens = quota_tokens
            if used_tokens is not None:
                account.used_tokens = used_tokens
            if role is not None:
                account.role = role
            account.updated_at = self._now()
            session.commit()
            return self._principal(account)
        finally:
            session.close()

    def record_usage(self, *, account_id: str, tokens: int) -> None:
        """Atomically add provider-reported tokens to an account's usage."""

        if tokens <= 0:
            return
        session = self.database.session()
        try:
            result = session.execute(
                update(AccountModel)
                .where(AccountModel.id == account_id)
                .values(
                    used_tokens=AccountModel.used_tokens + tokens,
                    updated_at=self._now(),
                )
            )
            if result.rowcount:
                session.commit()
        finally:
            session.close()

    def _create_session(self, session, account: AccountModel, now: datetime) -> AuthResult:
        token = secrets.token_urlsafe(self.auth_settings.token_bytes)
        auth_session = SessionModel(
            id=secrets.token_hex(16),
            account_id=account.id,
            token_hash=self._hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=self.database_settings.session_ttl_seconds),
        )
        session.add(auth_session)
        return AuthResult(account=self._principal(account), token=token)

    def _normalize_email(self, email: str) -> str:
        normalized = email.strip().lower()
        if len(normalized) > 320 or not EMAIL_PATTERN.fullmatch(normalized):
            raise AccountError("email_invalid")
        return normalized

    def _validate_password(self, password: str) -> None:
        if len(password) < self.auth_settings.password_min_length:
            raise AccountError("password_length_invalid")

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _principal(self, account: AccountModel) -> AccountPrincipal:
        role = account.role or "user"
        if account.email in self._configured_admin_emails():
            role = "admin"
        return AccountPrincipal(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            role=role,
            status=account.status or "active",
            quota_tokens=account.quota_tokens or 0,
            used_tokens=account.used_tokens or 0,
        )

    def _configured_admin_emails(self) -> set[str]:
        return {
            value.strip().lower()
            for value in self.auth_settings.admin_emails
            if value.strip()
        }

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
