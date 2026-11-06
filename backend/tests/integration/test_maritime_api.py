"""HTTP boundary checks against real account and office SQLite stores."""

from fastapi.testclient import TestClient

from backend.application.accounts.service import AccountService
from backend.config.settings import load_settings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import HarnessControlService
from backend.tests.integration.test_auth_api import CompletedExecutor


def test_office_authority_versions_and_revocation(tmp_path):
    settings = load_settings()
    settings.application["office_store_path"] = str(tmp_path / "office.db")
    settings.application["work_store_path"] = str(tmp_path / "work.db")
    db_settings = settings.database.model_copy(
        update={"url": f"sqlite:///{(tmp_path / 'accounts.db').as_posix()}"}
    )
    database = Database(db_settings)
    database.initialize()
    accounts = AccountService(database, db_settings, settings.auth)
    app = create_app(
        control_service=HarnessControlService(CompletedExecutor()),
        account_service=accounts,
        settings=settings,
    )
    try:
        with TestClient(app) as client:

            def register(email):
                response = client.post(
                    "/auth/register",
                    json={"email": email, "password": "maritime-test-password-12"},
                )
                assert response.status_code == 201
                return {
                    "Authorization": f"Bearer {response.json()['session']['token']}"
                }, response.json()["account"]["id"]

            owner, owner_id = register("owner@example.test")
            other, other_id = register("other@example.test")
            assert client.get("/maritime/skills").status_code == 401
            assert (
                len(client.get("/maritime/skills", headers=owner).json()["skills"]) == 8
            )
            spec = {
                "name": "私有工作法",
                "summary": "私人规范",
                "instructions": "客户机密",
                "visibility": "private",
            }
            saved = client.post("/maritime/skills", headers=owner, json=spec)
            assert saved.status_code == 201
            skill_id = saved.json()["id"]
            assert (
                client.get(f"/maritime/skills/{skill_id}", headers=other).status_code
                == 404
            )
            spec.update(
                instructions="可共享规范", visibility="community", expected_version=1
            )
            assert (
                client.put(
                    f"/maritime/skills/{skill_id}", headers=owner, json=spec
                ).status_code
                == 200
            )
            assert (
                client.put(
                    f"/maritime/skills/{skill_id}", headers=owner, json=spec
                ).status_code
                == 409
            )
            assert (
                client.get(
                    f"/maritime/skills/{skill_id}?version=1", headers=other
                ).status_code
                == 404
            )
            assert (
                client.get(f"/maritime/skills/{skill_id}", headers=other).json()[
                    "instructions"
                ]
                == "可共享规范"
            )
            case = client.post(
                "/maritime/cases",
                headers=owner,
                json={"title": "货损案件", "summary": "待核实"},
            ).json()
            case_id = case["id"]
            assert (
                client.get(f"/maritime/cases/{case_id}", headers=other).status_code
                == 404
            )

            def submit(headers, facts):
                cid = client.post(
                    "/workspace/conversations", headers=headers, json={}
                ).json()["conversation"]["conversation_id"]
                return client.post(
                    f"/workspace/conversations/{cid}/runs",
                    headers=headers,
                    json={
                        "conversation_id": cid,
                        "client_request_id": "first",
                        "text": "整理已知事实",
                        "facts": facts,
                        "constraints": [],
                        "material_ids": [],
                    },
                )

            resolved = submit(
                owner,
                {
                    "skill_id": skill_id,
                    "skill_context": {"instructions": "伪造"},
                    "case_id": case_id,
                    "case_context": {"title": "伪造"},
                },
            )
            assert resolved.status_code == 202
            facts = resolved.json()["run"]["payload"]["facts"]
            assert facts["skill_context"]["instructions"] == "可共享规范"
            assert facts["case_context"]["title"] == "货损案件"
            from backend.application.maritime.work_packages import (
                WorkPackageStore,
                validate_work,
            )

            package = validate_work(
                {
                    "skill_id": "case-assessment",
                    "title": "私人研判",
                    "sections": {
                        "relationships": [
                            {
                                "主体": "甲",
                                "身份": "待核",
                                "合同或行为": "待核",
                                "证据状态": "用户陈述",
                            }
                        ],
                        "hypotheses": [],
                    },
                }
            )
            current = resolved.json()["run"]
            WorkPackageStore(settings.application["work_store_path"]).save(
                current["run_id"], package
            )
            path = f"/maritime/runs/{current['run_id']}/deliverables?conversation_id={current['conversation_id']}"
            assert (
                client.get(path, headers=owner).json()["packages"][0]["title"]
                == "私人研判"
            )
            assert client.get(path, headers=other).status_code == 404
            assert client.get(path).status_code == 401
            assert submit(other, {"case_id": case_id}).status_code == 404
            invited = client.post(
                f"/maritime/cases/{case_id}/members",
                headers=owner,
                json={"email": "other@example.test", "role": "collaborator"},
            )
            assert invited.status_code == 200
            entry = {
                "kind": "document",
                "title": "索赔函草稿",
                "content": "待核对",
                "status": "reviewed",
            }
            assert (
                client.post(
                    f"/maritime/cases/{case_id}/entries", headers=other, json=entry
                ).status_code
                == 403
            )
            entry["status"] = "draft"
            draft = client.post(
                f"/maritime/cases/{case_id}/entries", headers=other, json=entry
            )
            assert draft.status_code == 201
            entry_id = draft.json()["id"]
            entry.update(status="reviewed", expected_version=1)
            assert (
                client.put(
                    f"/maritime/cases/{case_id}/entries/{entry_id}",
                    headers=owner,
                    json=entry,
                ).status_code
                == 200
            )
            assert (
                client.put(
                    f"/maritime/cases/{case_id}/entries/{entry_id}",
                    headers=owner,
                    json=entry,
                ).status_code
                == 409
            )
            history = client.get(
                f"/maritime/cases/{case_id}/entries/{entry_id}/versions", headers=other
            ).json()["versions"]
            assert [v["version"] for v in history] == [2, 1]
            assert (
                client.delete(
                    f"/maritime/cases/{case_id}/members/{other_id}", headers=owner
                ).status_code
                == 200
            )
            assert (
                client.get(f"/maritime/cases/{case_id}", headers=other).status_code
                == 404
            )
            assert (
                client.get(
                    f"/maritime/cases/{case_id}/entries/{entry_id}/versions",
                    headers=other,
                ).status_code
                == 404
            )
            body = {
                "items": [
                    {
                        "name": "货损",
                        "quantity": "3",
                        "rate": "0.335",
                        "currency": "USD",
                    }
                ]
            }
            assert client.post(
                "/maritime/claims/calculate", headers=owner, json=body
            ).json()["totals"] == {"USD": "1.01"}
            body["items"][0]["quantity"] = "NaN"
            assert (
                client.post(
                    "/maritime/claims/calculate", headers=owner, json=body
                ).status_code
                == 422
            )
    finally:
        database.close()
