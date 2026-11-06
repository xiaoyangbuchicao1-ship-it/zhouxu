"""Unit 1 account and identity boundary tests."""

from fastapi.testclient import TestClient

from backend.config.settings import load_settings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService


class CompletedExecutor:
    async def execute(self, run, control):
        from backend.contracts.modules.loop import RunResult

        return RunResult(
            run_id=run.run_id,
            status="completed",
            answer=f"完成：{run.payload.text}",
            elapsed_seconds=0,
        )


def account_client(tmp_path):
    settings = load_settings()
    database_settings = settings.database.model_copy(
        update={"url": f"sqlite:///{(tmp_path / 'accounts.db').as_posix()}"}
    )
    database = Database(database_settings)
    database.initialize()
    from backend.application.accounts.service import AccountService

    accounts = AccountService(database, database_settings, settings.auth)
    app = create_app(
        control_service=HarnessControlService(CompletedExecutor()),
        account_service=accounts,
    )
    return TestClient(app), database


def test_register_login_me_and_logout_are_real_database_operations(tmp_path):
    client, database = account_client(tmp_path)
    try:
        with client:
            registered = client.post(
                "/auth/register",
                json={
                    "email": " User@Example.com ",
                    "password": "long-password-12",
                    "display_name": "测试用户",
                },
            )
            assert registered.status_code == 201
            result = registered.json()
            assert result["account"]["email"] == "user@example.com"
            token = result["session"]["token"]
            assert token and token not in result["account"].values()

            duplicate = client.post(
                "/auth/register",
                json={"email": "user@example.com", "password": "long-password-12"},
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["detail"] == "account_email_exists"

            me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200 and me.json()["id"] == result["account"]["id"]
            login = client.post(
                "/auth/login",
                json={"email": "USER@example.com", "password": "long-password-12"},
            )
            assert login.status_code == 200
            login_token = login.json()["session"]["token"]
            assert login_token != token
            assert client.post(
                "/auth/logout", headers={"Authorization": f"Bearer {token}"}
            ).status_code == 204
            assert client.get(
                "/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code == 401
            assert client.get(
                "/auth/me", headers={"Authorization": f"Bearer {login_token}"}
            ).status_code == 200
    finally:
        database.close()


def test_harness_requires_a_valid_owner_token(tmp_path):
    client, database = account_client(tmp_path)
    try:
        with client:
            first = client.post(
                "/auth/register",
                json={"email": "first@example.com", "password": "long-password-12"},
            ).json()
            second = client.post(
                "/auth/register",
                json={"email": "second@example.com", "password": "long-password-12"},
            ).json()
            first_headers = {"Authorization": f"Bearer {first['session']['token']}"}
            second_headers = {"Authorization": f"Bearer {second['session']['token']}"}
            path = f"/harness/sessions/{first['account']['id']}/runs"
            accepted = client.post(path, json={"text": "问题"}, headers=first_headers)
            assert accepted.status_code == 202
            denied = client.post(path, json={"text": "越权"}, headers=second_headers)
            assert denied.status_code == 403
            assert denied.json()["detail"] == "session_access_denied"
            assert client.post(path, json={"text": "缺少令牌"}).status_code == 401
    finally:
        database.close()
