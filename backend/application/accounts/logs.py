"""Durable usage and audit log application service for the admin console."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    AuditLogModel,
    UsageLogModel,
)


class AccountLogService:
    """Keep usage records and security/admin events separate and queryable."""

    def __init__(self, database: Database):
        self.database = database

    def record_audit(
        self,
        *,
        category: str,
        action: str,
        actor_account_id: str | None = None,
        actor_email: str | None = None,
        actor_role: str = "anonymous",
        target_account_id: str | None = None,
        target_email: str | None = None,
        success: bool = True,
        status_code: int = 200,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Write safe metadata only; callers must never pass secrets or bodies."""

        session = self.database.session()
        try:
            session.add(
                AuditLogModel(
                    id=secrets.token_hex(16),
                    event_id=secrets.token_hex(16),
                    actor_account_id=actor_account_id,
                    actor_email=(actor_email or "")[:320] or None,
                    actor_role=actor_role[:24],
                    target_account_id=target_account_id,
                    target_email=(target_email or "")[:320] or None,
                    category=category[:24],
                    action=action[:128],
                    success=success,
                    status_code=status_code,
                    request_id=(request_id or "")[:64] or None,
                    ip_address=(ip_address or "")[:64] or None,
                    user_agent=(user_agent or "")[:512] or None,
                    details_json=json.dumps(details or {}, ensure_ascii=False),
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def record_usage_once(
        self,
        *,
        account_id: str,
        run_id: str,
        conversation_id: str | None,
        model: str,
        mode: str,
        status: str,
        total_tokens: int,
        created_at: datetime,
        completed_at: datetime | None,
    ) -> None:
        """Upsert a run's usage and apply only the token delta to the account."""

        total_tokens = max(0, int(total_tokens))
        session = self.database.session()
        try:
            existing = session.scalar(
                select(UsageLogModel).where(UsageLogModel.run_id == run_id)
            )
            previous = existing.total_tokens if existing else 0
            delta = max(0, total_tokens - previous)
            if existing is None:
                session.add(
                    UsageLogModel(
                        id=secrets.token_hex(16),
                        account_id=account_id,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        model=model[:80],
                        mode=mode[:16],
                        status=status[:24],
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=total_tokens,
                        created_at=created_at,
                        completed_at=completed_at,
                    )
                )
            else:
                existing.status = status[:24]
                existing.total_tokens = total_tokens
                existing.completed_at = completed_at or existing.completed_at
            if delta:
                session.execute(
                    update(AccountModel)
                    .where(AccountModel.id == account_id)
                    .values(
                        used_tokens=AccountModel.used_tokens + delta,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            session.commit()
        finally:
            session.close()

    def list_usage(
        self, *, search: str = "", status: str | None = None,
        offset: int = 0, limit: int = 50,
    ) -> tuple[list[dict], int]:
        session = self.database.session()
        try:
            query = select(UsageLogModel, AccountModel).join(
                AccountModel, AccountModel.id == UsageLogModel.account_id, isouter=True
            )
            value = search.strip().lower()
            if value:
                query = query.where(
                    AccountModel.email.contains(value)
                    | AccountModel.display_name.contains(value)
                    | UsageLogModel.run_id.contains(value)
                )
            if status:
                query = query.where(UsageLogModel.status == status)
            count_query = select(func.count()).select_from(UsageLogModel)
            if value or status:
                count_query = select(func.count()).select_from(query.subquery())
            total = int(session.scalar(count_query) or 0)
            rows = session.execute(
                query.order_by(UsageLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._usage_row(log, account) for log, account in rows], total
        finally:
            session.close()

    def list_audit(
        self, *, category: str | None = None, search: str = "",
        offset: int = 0, limit: int = 50,
    ) -> tuple[list[dict], int]:
        session = self.database.session()
        try:
            query = select(AuditLogModel)
            value = search.strip().lower()
            if category:
                query = query.where(AuditLogModel.category == category)
            if value:
                query = query.where(
                    AuditLogModel.action.contains(value)
                    | AuditLogModel.actor_email.contains(value)
                    | AuditLogModel.target_email.contains(value)
                    | AuditLogModel.target_account_id.contains(value)
                )
            count_query = select(func.count()).select_from(query.subquery())
            total = int(session.scalar(count_query) or 0)
            rows = session.scalars(
                query.order_by(AuditLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._audit_row(row) for row in rows], total
        finally:
            session.close()

    def list_user_activity(
        self, *, account_id: str, offset: int = 0, limit: int = 50,
    ) -> tuple[list[dict], int]:
        """Return a compact timeline made from account events and model runs.

        Usage records and audit records remain separate tables and endpoints;
        this projection is only the user-focused timeline shown in the admin
        console.
        """

        session = self.database.session()
        try:
            audit_rows = session.scalars(
                select(AuditLogModel)
                .where(
                    (AuditLogModel.actor_account_id == account_id)
                    | (AuditLogModel.target_account_id == account_id)
                )
                .order_by(AuditLogModel.created_at.desc())
                .limit(500)
            ).all()
            usage_rows = session.scalars(
                select(UsageLogModel)
                .where(UsageLogModel.account_id == account_id)
                .order_by(UsageLogModel.created_at.desc())
                .limit(500)
            ).all()
            timeline = [
                {
                    "id": row.id,
                    "kind": "audit",
                    "account_id": account_id,
                    "email": row.target_email or row.actor_email,
                    "action": row.action,
                    "status": "success" if row.success else "failed",
                    "ip_address": row.ip_address,
                    "details": row.details_json,
                    "created_at": row.created_at,
                }
                for row in audit_rows
            ]
            timeline.extend(
                {
                    "id": row.id,
                    "kind": "usage",
                    "account_id": row.account_id,
                    "email": None,
                    "action": "run.usage",
                    "status": row.status,
                    "ip_address": None,
                    "details": json.dumps(
                        {
                            "run_id": row.run_id,
                            "model": row.model,
                            "mode": row.mode,
                            "total_tokens": row.total_tokens,
                        },
                        ensure_ascii=False,
                    ),
                    "created_at": row.created_at,
                }
                for row in usage_rows
            )
            timeline.sort(key=lambda item: item["created_at"], reverse=True)
            total = len(timeline)
            page = timeline[offset : offset + limit]
            for item in page:
                try:
                    item["details"] = json.loads(item["details"] or "{}")
                except (TypeError, ValueError):
                    item["details"] = {}
            return page, total
        finally:
            session.close()

    @staticmethod
    def _usage_row(log: UsageLogModel, account: AccountModel | None) -> dict:
        return {
            "id": log.id,
            "account_id": log.account_id,
            "email": account.email if account else None,
            "display_name": account.display_name if account else None,
            "run_id": log.run_id,
            "conversation_id": log.conversation_id,
            "model": log.model,
            "mode": log.mode,
            "status": log.status,
            "input_tokens": log.input_tokens,
            "output_tokens": log.output_tokens,
            "total_tokens": log.total_tokens,
            "created_at": log.created_at,
            "completed_at": log.completed_at,
        }

    @staticmethod
    def _audit_row(log: AuditLogModel) -> dict:
        try:
            details = json.loads(log.details_json or "{}")
        except (TypeError, ValueError):
            details = {}
        return {
            "id": log.id,
            "event_id": log.event_id,
            "actor_account_id": log.actor_account_id,
            "actor_email": log.actor_email,
            "actor_role": log.actor_role,
            "target_account_id": log.target_account_id,
            "target_email": log.target_email,
            "category": log.category,
            "action": log.action,
            "success": log.success,
            "status_code": log.status_code,
            "request_id": log.request_id,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "details": details,
            "created_at": log.created_at,
        }
