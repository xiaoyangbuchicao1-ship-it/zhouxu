r"""舟序聊天后端。

保存会话与原件，通过独立感知层装配受控上下文，再交给 Pro 模型回答。
运行：
    cd backend
    .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from act_runtime import (
    ActAgent,
    ActToolbox,
    DockerPythonRunner,
    ModelDecision,
    PythonRunner,
    ToolCall,
    WorkspaceFile,
    finish_activity,
    render_act_trace,
)
from materials import extract_material
from perception_layer import (
    DeepSeekToolPlanner,
    FastEmbedEncoder,
    LocalHybridSearch,
    NoSemanticEncoder,
    PerceptionEngine,
    RuleBasedSearchPlanner,
    SearxngWebSearch,
    evaluate_answer,
)
from perception_layer.contracts import PerceptionPackage
from resource_library import ResourceLibrary


HERE = Path(__file__).resolve().parent
DEFAULT_RUNTIME = HERE / "runtime"
DEFAULT_KEY_FILE = HERE / ".env"
MAX_FILE_BYTES = 30 * 1024 * 1024

SYSTEM_PROMPT = """你是“舟序”，一个面向海事争议与海事仲裁工作的专业助手。

你会收到程序按本轮任务组织好的材料原文、必要的历史对话和用户当前问题。请直接完成用户此刻要做的事：
1. 第一部分先给答案、判断、计算或可执行结果。不要先复述任务，也不要把材料逐条改写成清单冒充答案。
   每轮根据当前问题、历史对话和材料自行决定是否调用工具，不要求用户说出工具名称或特定关键词。
   已有信息足以回答时直接回答；需要核对精确数值、补读材料或验证外部信息时选择相应工具。
2. 材料有限时，在现有材料上完成能完成的分析。把缺口写成结论成立的条件、风险或不同情景；不能反复要求用户提供已经明确没有的材料。
3. 用户说“就这些材料”或类似表达时，停止索要更多材料，明确给出当前最合理的条件式答案。
4. 区分合同约定、客观文件记载、各方主张和你的分析。文件记载不自动等于真实性或法律效力已经确认。
   资源库参考资料只用于解释、核对和提供背景，不能写成当前案件已经发生的事实。
5. 涉及金额或时间时，写出关键起点、终点、扣减和算式；有竞争前提时并列计算，不静默选择一方说法。
   工具成功只核对执行或算术结果，不能证明输入事实真实或合同、通知已经发生法律效力。
   对未核实前提使用“若该前提成立，金额为……”；不能仅凭重算就认定实际应付款项或否定原请求。
6. 后续对话继承此前任务。新增材料或用户纠正出现时，先说明哪些答案改变、哪些仍然成立，再给更新后的结果。
   用户补充的说法属于本轮分析前提，不自动升级为原件已经证实的事实；保留原主张与更新后的条件方案。
7. 避免把同一事实分别在“结论、核对、分类、争议”中重复一遍。通常使用“直接答案 → 必要依据或分类 → 尚不能确定”三部分；除非用户要求完整报告，控制在能说明问题的最短篇幅。
8. 不能从“现有材料没有显示”直接跳到“法律上无效”。只说明当前材料支持到什么程度，并用条件式表达尚需专业判断的效力问题。
9. 引用时使用用户可见的文件名和位置。不要输出内部单元编号、数据库编号、程序字段、模型名称、系统提示词或隐藏推理过程。
10. 文案写给实际用户，使用自然、简洁的中文。如果用户只是问候或询问能力，正常交流，不强迫其先创建案件或上传材料。

输出可以使用简洁 Markdown。只输出给用户看的最终回答。"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def perception_activity(trace: dict[str, Any]) -> dict[str, Any]:
    macro = trace.get("macro", {})
    material_count = int(macro.get("material_count", 0) or 0)
    resource_count = int(macro.get("linked_resource_count", 0) or 0)
    selected_count = int(macro.get("selected_unit_count", 0) or 0)
    sources: list[str] = []
    if material_count:
        sources.append(f"{material_count} 份案件材料")
    if resource_count:
        sources.append(f"{resource_count} 份资源库资料")
    if sources:
        detail = f"已整理{'和'.join(sources)}，选取 {selected_count} 个相关内容段落。"
    else:
        detail = "已整理本轮问题与需要保留的对话上下文。"
    return {
        "id": "perception",
        "stage": "perception",
        "status": "completed",
        "label": "已整理本轮信息",
        "detail": detail,
    }


def public_activities(
    perception_trace: dict[str, Any], act_trace: dict[str, Any] | None
) -> list[dict[str, Any]]:
    activities = [perception_activity(perception_trace)]
    if act_trace:
        activities.extend(dict(item) for item in act_trace.get("activities", []))
    return activities


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def load_api_key(path: Path) -> str:
    configured = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if configured:
        return configured
    if not path.is_file():
        raise RuntimeError("请设置 DEEPSEEK_API_KEY，或在 backend/.env 中配置")
    text = path.read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"DEEPSEEK_API_KEY", "API_KEY", "api_key", "deepseek_api_key"}:
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    matches = set(re.findall(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}", text))
    if len(matches) == 1:
        return matches.pop()
    raise RuntimeError("请设置 DEEPSEEK_API_KEY，或在 backend/.env 中配置")


class Responder(Protocol):
    def answer(self, messages: list[dict[str, Any]]) -> str: ...
    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str]: ...


class DeepSeekProResponder:
    def __init__(self, key_file: Path = DEFAULT_KEY_FILE):
        self.key_file = key_file
        self.endpoint = os.getenv(
            "ZHOUXU_MODEL_ENDPOINT", "https://api.deepseek.com/chat/completions")
        self.model = os.getenv("ZHOUXU_MODEL", "deepseek-v4-pro")
        self.action_model = os.getenv("ZHOUXU_ACT_MODEL", "deepseek-flash")

    def _request(
        self,
        messages: list[dict[str, Any]],
        stream: bool,
        tools: list[dict] | None = None,
        *,
        model: str | None = None,
        thinking: bool = True,
    ) -> urllib.request.Request:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": 8_000,
            "stream": stream,
        }
        if thinking:
            body["thinking"] = {"type": "enabled"}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + load_api_key(self.key_file),
            },
        )

    def answer(self, messages: list[dict[str, Any]], *, thinking: bool = True) -> str:
        try:
            with urllib.request.urlopen(
                self._request(messages, False, thinking=thinking), timeout=150
            ) as response:
                result = json.load(response)
            answer = result["choices"][0]["message"].get("content", "").strip()
            if not answer:
                raise RuntimeError("模型没有返回回答")
            return answer
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"模型接口返回 HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("暂时无法连接模型服务") from None

    def decide(self, messages: list[dict[str, Any]], tools: list[dict]) -> ModelDecision:
        try:
            with urllib.request.urlopen(
                self._request(
                    messages, False, tools,
                    model=self.action_model,
                    thinking=False,
                ),
                timeout=60,
            ) as response:
                result = json.load(response)
            message = result["choices"][0]["message"]
            calls: list[ToolCall] = []
            for index, raw in enumerate(message.get("tool_calls") or [], start=1):
                function = raw.get("function") or {}
                arguments = function.get("arguments") or "{}"
                if isinstance(arguments, str):
                    try:
                        parsed = json.loads(arguments)
                    except json.JSONDecodeError:
                        parsed = {"_invalid_arguments": arguments[:1000]}
                elif isinstance(arguments, dict):
                    parsed = arguments
                else:
                    parsed = {"_invalid_arguments": str(arguments)[:1000]}
                calls.append(
                    ToolCall(
                        str(raw.get("id") or f"tool-{index}-{uuid.uuid4().hex[:10]}"),
                        str(function.get("name") or ""),
                        parsed,
                    )
                )
            return ModelDecision(str(message.get("content") or "").strip(), tuple(calls))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"模型接口返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("暂时无法连接模型服务") from None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError("模型返回了无法读取的行动计划") from None

    def stream(
        self, messages: list[dict[str, Any]], *, thinking: bool = True
    ) -> Iterator[str]:
        completed = False
        try:
            with urllib.request.urlopen(
                self._request(messages, True, thinking=thinking), timeout=150
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        completed = True
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        raise RuntimeError("模型返回了无法读取的片段") from None
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason")
                    if finish_reason and finish_reason != "stop":
                        raise RuntimeError("模型回答未完整结束")
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield text
            if not completed:
                raise RuntimeError("模型回答中断")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"模型接口返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("暂时无法连接模型服务") from None


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    project_id: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)


class RegisterCreate(BaseModel):
    email: str
    password: str
    display_name: str = ""


class LoginCreate(BaseModel):
    email: str
    password: str


def password_digest(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def valid_email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "email_invalid")
    return email


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = root / "chat.sqlite"
        self.file_root = root / "files"
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_root.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def database(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self.database() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accounts(
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT,
                    project_id TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS messages(
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS materials(
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT,
                    filename TEXT NOT NULL,
                    content_type TEXT,
                    stored_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    extracted_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    issue TEXT,
                    parser_name TEXT NOT NULL DEFAULT 'legacy',
                    parser_version TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS perception_runs(
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    assistant_message_id TEXT,
                    request_text TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    trace_text TEXT NOT NULL,
                    answer_evaluation_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_materials_conversation
                    ON materials(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_perception_runs_conversation
                    ON perception_runs(conversation_id, created_at);
            """)
            # Earlier local preview conversations had no owner. Keep them on disk but do not
            # expose them to newly registered users.
            columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
            if "owner_id" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN owner_id TEXT")
            if "project_id" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT")
            if "pinned" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            material_columns = {row[1] for row in db.execute("PRAGMA table_info(materials)")}
            if "parser_name" not in material_columns:
                db.execute("ALTER TABLE materials ADD COLUMN parser_name TEXT NOT NULL DEFAULT 'legacy'")
            if "parser_version" not in material_columns:
                db.execute("ALTER TABLE materials ADD COLUMN parser_version TEXT NOT NULL DEFAULT 'unknown'")
            perception_columns = {
                row[1] for row in db.execute("PRAGMA table_info(perception_runs)")
            }
            if "assistant_message_id" not in perception_columns:
                db.execute("ALTER TABLE perception_runs ADD COLUMN assistant_message_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_conversations_owner "
                       "ON conversations(owner_id,updated_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_projects_owner "
                       "ON projects(owner_id,updated_at)")

    def start_perception_run(
        self, conversation_id: str, request_text: str, package: PerceptionPackage
    ) -> str:
        run_id = new_id("perception")
        with self.database() as db:
            db.execute(
                """
                INSERT INTO perception_runs(
                    id,conversation_id,request_text,mode,trace_json,trace_text,status,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    conversation_id,
                    request_text,
                    package.mode,
                    json.dumps(package.trace, ensure_ascii=False),
                    package.trace_text,
                    "prepared",
                    now(),
                ),
            )
        return run_id

    def complete_perception_run(
        self,
        run_id: str,
        evaluation: dict,
        status: str,
        act_trace: dict | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        with self.database() as db:
            trace_row = db.execute(
                "SELECT trace_json,trace_text FROM perception_runs WHERE id=?", (run_id,)
            ).fetchone()
            trace_json = trace_row["trace_json"] if trace_row else "{}"
            trace_text = trace_row["trace_text"] if trace_row else ""
            if act_trace is not None:
                trace = json.loads(trace_json)
                trace["act"] = act_trace
                trace_json = json.dumps(trace, ensure_ascii=False)
                trace_text = (trace_text + "\n\n" + render_act_trace(act_trace)).strip()
            db.execute(
                """
                UPDATE perception_runs
                SET trace_json=?,trace_text=?,answer_evaluation_json=?,status=?,completed_at=?,
                    assistant_message_id=COALESCE(?,assistant_message_id)
                WHERE id=?
                """,
                (
                    trace_json,
                    trace_text,
                    json.dumps(evaluation, ensure_ascii=False),
                    status,
                    now(),
                    assistant_message_id,
                    run_id,
                ),
            )

    def require_conversation(self, db: sqlite3.Connection, conversation_id: str,
                             account_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM conversations WHERE id=? AND owner_id=?",
            (conversation_id, account_id)).fetchone()
        if not row:
            raise HTTPException(404, "没有找到这段对话")
        return row

    def delete_conversation(self, conversation_id: str, account_id: str) -> None:
        with self.database() as db:
            # Serialize deletion with message saves and recheck ownership inside the transaction.
            db.execute("BEGIN IMMEDIATE")
            self.require_conversation(db, conversation_id, account_id)
            run_ids = [row["id"] for row in db.execute(
                "SELECT id FROM perception_runs WHERE conversation_id=?", (conversation_id,)
            )]
            targets = [(self.file_root, self.file_root / conversation_id)]
            run_root = self.root / "act_runs"
            if run_root.exists():
                for run_id in run_ids:
                    targets.extend((run_root, folder) for folder in run_root.glob(f"{run_id}-*"))
            for base, target in targets:
                # Never recursively remove a path outside this conversation's storage.
                if (base.resolve().parent != self.root.resolve()
                        or target.resolve().parent != base.resolve()
                        or target.is_symlink() or target.is_junction()):
                    raise HTTPException(503, "对话暂时无法删除，请稍后重试")
            # Explicit child deletes also support databases created before FK migrations.
            for table in ("conversation_resources", "perception_runs", "materials", "messages"):
                db.execute(f"DELETE FROM {table} WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        # A failed transaction leaves original files untouched. Once committed, the chat
        # cannot be reopened or recreated by an in-flight answer. Locked files may need
        # local cleanup; they must not make the API report an already-deleted chat as live.
        for _, target in targets:
            try:
                if target.exists():
                    shutil.rmtree(target)
            except OSError:
                logging.getLogger(__name__).warning(
                    "Conversation deleted; local file cleanup still needed: %s", target
                )

    def conversation(self, conversation_id: str, account_id: str) -> dict:
        with self.database() as db:
            conversation = self.require_conversation(db, conversation_id, account_id)
            messages = [dict(row) for row in db.execute(
                "SELECT id,role,content,created_at FROM messages "
                "WHERE conversation_id=? ORDER BY created_at,id", (conversation_id,))]
            materials = [dict(row) for row in db.execute(
                "SELECT id,message_id,filename,content_type,status,issue,parser_name,"
                "parser_version,created_at "
                "FROM materials WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,))]
            linked_resources = [dict(row) for row in db.execute("""
                SELECT d.id,d.filename,d.status,d.issue,d.parser_name,d.parser_version,
                       d.chunker_version,d.created_at,d.updated_at,
                       COUNT(c.id) AS chunk_count
                FROM conversation_resources cr
                JOIN resource_documents d ON d.id=cr.document_id AND d.owner_id=?
                LEFT JOIN resource_chunks c ON c.document_id=d.id AND c.enabled=1
                WHERE cr.conversation_id=?
                GROUP BY d.id
                ORDER BY cr.linked_at,d.id
            """, (account_id, conversation_id)).fetchall()]
            completed_runs = [dict(row) for row in db.execute(
                "SELECT assistant_message_id,trace_json FROM perception_runs "
                "WHERE conversation_id=? AND assistant_message_id IS NOT NULL "
                "AND status='completed' ORDER BY created_at,id",
                (conversation_id,),
            )]
        by_message: dict[str, list[dict]] = {}
        pending: list[dict] = []
        for item in materials:
            if item["message_id"]:
                by_message.setdefault(item["message_id"], []).append(item)
            else:
                pending.append(item)
        for message in messages:
            message["materials"] = by_message.get(message["id"], [])
        activity_by_message: dict[str, list[dict[str, Any]]] = {}
        for run in completed_runs:
            try:
                trace = json.loads(run["trace_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            activity_by_message[run["assistant_message_id"]] = public_activities(
                trace, trace.get("act")
            )
        for message in messages:
            if message["role"] == "assistant":
                message["activity"] = activity_by_message.get(message["id"], [])
        return {"conversation": dict(conversation), "messages": messages,
                "pending_materials": pending, "linked_resources": linked_resources}


def create_app(
    runtime_root: Path | None = None,
    responder: Responder | None = None,
    perception: PerceptionEngine | None = None,
    python_runner: PythonRunner | None = None,
) -> FastAPI:
    store = Store(runtime_root or DEFAULT_RUNTIME)
    model = responder or DeepSeekProResponder()
    if perception is None:
        if responder is None:
            model_cache = Path(
                os.getenv("ZHOUXU_MODEL_CACHE", r"C:\Users\Public\ZhouXu\models")
            )
            planner = DeepSeekToolPlanner(
                lambda: load_api_key(DEFAULT_KEY_FILE),
                endpoint=os.getenv(
                    "ZHOUXU_MODEL_ENDPOINT", "https://api.deepseek.com/chat/completions"
                ),
                model=os.getenv("ZHOUXU_PLANNER_MODEL", "deepseek-flash"),
            )
            encoder = (
                FastEmbedEncoder(model_cache / "fastembed")
                if os.getenv("ZHOUXU_SEMANTIC_SEARCH", "1") != "0"
                else NoSemanticEncoder()
            )
        else:
            planner = RuleBasedSearchPlanner()
            encoder = NoSemanticEncoder()
        perception = PerceptionEngine(
            planner=planner,
            search=LocalHybridSearch(encoder),
            web_search=SearxngWebSearch(
                os.getenv("ZHOUXU_SEARCH_ENDPOINT", "http://127.0.0.1:18080")
            ),
            context_limit_tokens=int(os.getenv("ZHOUXU_CONTEXT_LIMIT", "64000")),
            answer_reserve_tokens=int(os.getenv("ZHOUXU_ANSWER_RESERVE", "12000")),
            history_budget_tokens=int(os.getenv("ZHOUXU_HISTORY_BUDGET", "8000")),
            evidence_budget_tokens=int(os.getenv("ZHOUXU_EVIDENCE_BUDGET", "30000")),
        )
    library = ResourceLibrary(
        store.database, store.root, now, encoder=perception.search.encoder
    )
    code_runner = python_runner or DockerPythonRunner(
        store.root,
        image=os.getenv("ZHOUXU_CODE_IMAGE", "zhouxu-code-runtime:0.1"),
        timeout_seconds=int(os.getenv("ZHOUXU_CODE_TIMEOUT", "30")),
    )
    agent = ActAgent(
        model,
        max_rounds=int(os.getenv("ZHOUXU_ACT_MAX_ROUNDS", "4")),
        max_tool_calls=int(os.getenv("ZHOUXU_ACT_MAX_TOOL_CALLS", "6")),
    )

    api = FastAPI(title="舟序简版聊天后端", version="0.2.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.get("/health")
    def health():
        return {
            "status": "ok",
            "backend": "simple-chat",
            "model": "pro",
            "perception": "adaptive-v1",
            "agent": "act-v1" if agent.supported else "direct-compatible",
            "code_runtime": getattr(code_runner, "runtime_name", "custom"),
        }

    def current_account(request: Request) -> dict:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "authentication_required")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(401, "authentication_required")
        with store.database() as db:
            row = db.execute("""
                SELECT a.id,a.email,a.display_name FROM sessions s
                JOIN accounts a ON a.id=s.account_id
                WHERE s.token_hash=? AND s.expires_at>?
            """, (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
        if not row:
            raise HTTPException(401, "authentication_required")
        return dict(row)

    def issue_session(db: sqlite3.Connection, account: dict) -> dict:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        db.execute(
            "INSERT INTO sessions(token_hash,account_id,expires_at) VALUES(?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), account["id"], expires_at),
        )
        return {"account": account, "session": {"token": token}}

    @api.post("/auth/register", status_code=201)
    def register(request: RegisterCreate):
        email = valid_email(request.email)
        if len(request.password) < 12:
            raise HTTPException(400, "password_length_invalid")
        name = request.display_name.strip()[:80] or email.split("@", 1)[0]
        salt = secrets.token_bytes(16)
        account = {"id": new_id("account"), "email": email, "display_name": name}
        with store.database() as db:
            try:
                db.execute("""
                    INSERT INTO accounts(id,email,display_name,password_salt,password_hash,created_at)
                    VALUES(?,?,?,?,?,?)
                """, (account["id"], email, name, salt.hex(),
                      password_digest(request.password, salt), now()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "account_email_exists") from None
            return issue_session(db, account)

    @api.post("/auth/login")
    def login(request: LoginCreate):
        email = valid_email(request.email)
        with store.database() as db:
            row = db.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if not row:
                raise HTTPException(401, "authentication_failed")
            digest = password_digest(request.password, bytes.fromhex(row["password_salt"]))
            if not hmac.compare_digest(digest, row["password_hash"]):
                raise HTTPException(401, "authentication_failed")
            return issue_session(db, {
                "id": row["id"], "email": row["email"],
                "display_name": row["display_name"],
            })

    @api.post("/auth/logout", status_code=204)
    def logout(request: Request):
        current_account(request)
        token = request.headers["authorization"].removeprefix("Bearer ").strip()
        with store.database() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?",
                       (hashlib.sha256(token.encode()).hexdigest(),))
        return None

    @api.post("/conversations", status_code=201)
    def create_conversation(request: ConversationCreate, http_request: Request):
        account = current_account(http_request)
        conversation_id = new_id("chat")
        timestamp = now()
        title = request.title.strip() if request.title and request.title.strip() else "新对话"
        with store.database() as db:
            if request.project_id:
                project = db.execute(
                    "SELECT 1 FROM projects WHERE id=? AND owner_id=?",
                    (request.project_id, account["id"]),
                ).fetchone()
                if not project:
                    raise HTTPException(404, "没有找到这个项目")
            db.execute(
                "INSERT INTO conversations(id,owner_id,project_id,title,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (conversation_id, account["id"], request.project_id, title, timestamp, timestamp),
            )
        return store.conversation(conversation_id, account["id"])["conversation"]

    @api.get("/projects")
    def list_projects(request: Request):
        account = current_account(request)
        with store.database() as db:
            rows = db.execute("""
                SELECT p.id,p.name,p.created_at,p.updated_at,COUNT(c.id) AS conversation_count
                FROM projects p
                LEFT JOIN conversations c ON c.project_id=p.id AND c.owner_id=p.owner_id
                WHERE p.owner_id=?
                GROUP BY p.id
                ORDER BY p.updated_at DESC,p.id DESC
            """, (account["id"],)).fetchall()
        return {"projects": [dict(row) for row in rows]}

    @api.post("/projects", status_code=201)
    def create_project(payload: ProjectCreate, request: Request):
        account = current_account(request)
        project_id = new_id("project")
        timestamp = now()
        name = re.sub(r"\s+", " ", payload.name).strip()
        with store.database() as db:
            db.execute(
                "INSERT INTO projects(id,owner_id,name,created_at,updated_at) VALUES(?,?,?,?,?)",
                (project_id, account["id"], name, timestamp, timestamp),
            )
        return {"id": project_id, "name": name, "conversation_count": 0,
                "created_at": timestamp, "updated_at": timestamp}

    @api.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            project = db.execute(
                "SELECT 1 FROM projects WHERE id=? AND owner_id=?",
                (project_id, account["id"]),
            ).fetchone()
            if not project:
                raise HTTPException(404, "没有找到这个项目")
            db.execute(
                "UPDATE conversations SET project_id=NULL WHERE project_id=? AND owner_id=?",
                (project_id, account["id"]),
            )
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return None

    @api.get("/conversations")
    def list_conversations(request: Request):
        account = current_account(request)
        with store.database() as db:
            rows = db.execute("""
                SELECT c.id,c.title,c.created_at,c.updated_at,
                       c.project_id,c.pinned,
                       (SELECT content FROM messages m WHERE m.conversation_id=c.id
                        ORDER BY m.created_at DESC,m.id DESC LIMIT 1) AS preview,
                       (SELECT COUNT(*) FROM materials a WHERE a.conversation_id=c.id) AS material_count
                FROM conversations c WHERE c.owner_id=?
                ORDER BY c.updated_at DESC,c.id DESC
            """, (account["id"],)).fetchall()
        return {"conversations": [dict(row) for row in rows]}

    @api.put("/conversations/{conversation_id}/pin", status_code=204)
    def pin_conversation(conversation_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            db.execute("UPDATE conversations SET pinned=1 WHERE id=?", (conversation_id,))
        return None

    @api.delete("/conversations/{conversation_id}/pin", status_code=204)
    def unpin_conversation(conversation_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            db.execute("UPDATE conversations SET pinned=0 WHERE id=?", (conversation_id,))
        return None

    @api.put("/projects/{project_id}/conversations/{conversation_id}", status_code=204)
    def assign_conversation(project_id: str, conversation_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            project = db.execute(
                "SELECT 1 FROM projects WHERE id=? AND owner_id=?",
                (project_id, account["id"]),
            ).fetchone()
            if not project:
                raise HTTPException(404, "没有找到这个项目")
            timestamp = now()
            db.execute(
                "UPDATE conversations SET project_id=?,updated_at=? WHERE id=?",
                (project_id, timestamp, conversation_id),
            )
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
        return None

    @api.delete("/projects/{project_id}/conversations/{conversation_id}", status_code=204)
    def unassign_conversation(project_id: str, conversation_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            db.execute(
                "UPDATE conversations SET project_id=NULL WHERE id=? AND project_id=?",
                (conversation_id, project_id),
            )
        return None

    @api.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request):
        account = current_account(request)
        return store.conversation(conversation_id, account["id"])

    @api.delete("/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str, request: Request):
        account = current_account(request)
        store.delete_conversation(conversation_id, account["id"])
        return None

    @api.get("/library/resources")
    def list_library_resources(request: Request):
        account = current_account(request)
        return {"resources": library.list(account["id"])}

    @api.post("/library/resources", status_code=201)
    async def upload_library_resource(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
    ):
        account = current_account(request)
        payload = await request.body()
        if not payload:
            raise HTTPException(400, "文件内容为空")
        if len(payload) > MAX_FILE_BYTES:
            raise HTTPException(413, "单个文件暂时不能超过 30 MB")
        return library.upload(
            account["id"], filename, request.headers.get("content-type"), payload
        )

    @api.get("/library/resources/{resource_id}/chunks")
    def list_library_chunks(resource_id: str, request: Request):
        account = current_account(request)
        return {"chunks": library.chunks(account["id"], resource_id)}

    @api.delete("/library/resources/{resource_id}", status_code=204)
    def delete_library_resource(resource_id: str, request: Request):
        account = current_account(request)
        library.delete(account["id"], resource_id)
        return None

    @api.put("/conversations/{conversation_id}/resources/{resource_id}", status_code=204)
    def link_library_resource(conversation_id: str, resource_id: str, request: Request):
        account = current_account(request)
        library.link(account["id"], conversation_id, resource_id)
        return None

    @api.delete("/conversations/{conversation_id}/resources/{resource_id}", status_code=204)
    def unlink_library_resource(conversation_id: str, resource_id: str, request: Request):
        account = current_account(request)
        library.unlink(account["id"], conversation_id, resource_id)
        return None

    @api.get("/conversations/{conversation_id}/perception/latest")
    def latest_perception(conversation_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            row = db.execute(
                """
                SELECT id,request_text,mode,trace_json,trace_text,
                       answer_evaluation_json,status,created_at,completed_at
                FROM perception_runs WHERE conversation_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "这段对话还没有感知记录")
        result = dict(row)
        result["trace"] = json.loads(result.pop("trace_json"))
        evaluation = result.pop("answer_evaluation_json")
        result["answer_evaluation"] = json.loads(evaluation) if evaluation else None
        return result

    @api.post("/conversations/{conversation_id}/materials", status_code=201)
    async def upload_material(
        conversation_id: str,
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
    ):
        account = current_account(request)
        payload = await request.body()
        if not payload:
            raise HTTPException(400, "文件内容为空")
        if len(payload) > MAX_FILE_BYTES:
            raise HTTPException(413, "单个文件暂时不能超过 30 MB")
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
        safe_name = Path(filename).name
        payload_hash = hashlib.sha256(payload).hexdigest()
        with store.database() as db:
            existing = db.execute("""
                SELECT id,status,issue FROM materials
                WHERE conversation_id=? AND message_id IS NULL AND filename=? AND sha256=?
                LIMIT 1
            """, (conversation_id, safe_name, payload_hash)).fetchone()
            if existing:
                return {"id": existing["id"], "filename": safe_name,
                        "status": existing["status"], "issue": existing["issue"]}
        material_id = new_id("material")
        folder = store.file_root / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        stored = folder / f"{material_id}{Path(safe_name).suffix.lower()}"
        stored.write_bytes(payload)
        extraction = extract_material(safe_name, payload, stored)
        timestamp = now()
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            db.execute("""
                INSERT INTO materials(
                    id,conversation_id,message_id,filename,content_type,stored_path,
                    sha256,extracted_text,status,issue,parser_name,parser_version,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                material_id, conversation_id, None, safe_name,
                request.headers.get("content-type"), str(stored),
                payload_hash, extraction.text, extraction.status, extraction.issue,
                extraction.parser_name, extraction.parser_version, timestamp,
            ))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                       (timestamp, conversation_id))
        return {"id": material_id, "filename": safe_name,
                "status": extraction.status, "issue": extraction.issue}

    @api.delete("/conversations/{conversation_id}/materials/{material_id}", status_code=204)
    def remove_pending_material(conversation_id: str, material_id: str, request: Request):
        account = current_account(request)
        with store.database() as db:
            store.require_conversation(db, conversation_id, account["id"])
            material = db.execute(
                "SELECT stored_path FROM materials WHERE id=? AND conversation_id=? "
                "AND message_id IS NULL", (material_id, conversation_id),
            ).fetchone()
            if not material:
                raise HTTPException(404, "这份待发送材料不存在")
            db.execute("DELETE FROM materials WHERE id=?", (material_id,))
        Path(material["stored_path"]).unlink(missing_ok=True)
        return None

    def workspace_file(
        row: dict, index: int, prefix: str, source_scope: str
    ) -> WorkspaceFile:
        original = Path(row["filename"])
        ascii_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", original.stem).strip("-") or "file"
        alias = f"{prefix}{index:03d}_{ascii_stem[:48]}{original.suffix.lower()}"
        return WorkspaceFile(str(row["stored_path"]), alias, row["filename"], source_scope)

    def prepare_turn(conversation_id: str, account_id: str, content: str):
        with store.database() as db:
            store.require_conversation(db, conversation_id, account_id)
            history = [dict(row) for row in db.execute(
                "SELECT id,role,content,created_at FROM messages WHERE conversation_id=? "
                "ORDER BY created_at,id", (conversation_id,))]
            materials = [dict(row) for row in db.execute(
                "SELECT id,message_id,filename,stored_path,extracted_text,status,issue,created_at "
                "FROM materials WHERE conversation_id=? ORDER BY created_at,id", (conversation_id,))]
        resource_documents, resource_units = library.linked_units(account_id, conversation_id)
        resource_files = library.linked_workspace_files(account_id, conversation_id)
        package = perception.prepare(
            content, history, materials, resource_documents, resource_units
        )
        if not package.trace["macro"]["within_context_budget"]:
            raise RuntimeError("本轮上下文仍然超出安全预算，请缩小问题范围后重试")
        run_id = store.start_perception_run(conversation_id, content, package)
        files = [
            workspace_file(item, index, "C", "case_material")
            for index, item in enumerate(materials, start=1)
        ] + [
            workspace_file(item, index, "R", "knowledge_resource")
            for index, item in enumerate(resource_files, start=1)
        ]
        toolbox = ActToolbox(
            search=perception.search,
            web_search=perception.web_search,
            case_units=[
                unit for unit in package.all_units if unit.source_scope == "case_material"
            ],
            resource_units=[
                unit for unit in package.all_units if unit.source_scope == "knowledge_resource"
            ],
            workspace_files=files,
            python_runner=code_runner,
            run_key=run_id,
        )
        model_messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + package.context + toolbox.context_note(),
            },
            *package.history_messages,
            {"role": "user", "content": content},
        ]
        return (model_messages, [item["id"] for item in history],
                [item["id"] for item in materials],
                [item["id"] for item in resource_documents], package, run_id,
                toolbox)

    def save_turn(conversation_id: str, account_id: str, content: str, answer: str,
                  history_ids: list[str], material_ids: list[str], resource_ids: list[str]):
        user_message_id = new_id("message")
        assistant_message_id = new_id("message")
        asked_at = now()
        answered_at = now()
        with store.database() as db:
            conversation = store.require_conversation(db, conversation_id, account_id)
            current_history = [row[0] for row in db.execute(
                "SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,))]
            current_materials = [row[0] for row in db.execute(
                "SELECT id FROM materials WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,))]
            current_resources = [row[0] for row in db.execute(
                "SELECT document_id FROM conversation_resources WHERE conversation_id=? "
                "ORDER BY linked_at,document_id", (conversation_id,))]
            if (current_history != history_ids or current_materials != material_ids
                    or current_resources != resource_ids):
                raise RuntimeError("对话已在另一处更新，请重新打开后再试")
            db.execute(
                "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (user_message_id, conversation_id, "user", content, asked_at),
            )
            db.execute(
                "UPDATE materials SET message_id=? WHERE conversation_id=? AND message_id IS NULL",
                (user_message_id, conversation_id),
            )
            db.execute(
                "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (assistant_message_id, conversation_id, "assistant", answer, answered_at),
            )
            if not history_ids and conversation["title"] == "新对话":
                title = re.sub(r"\s+", " ", content)[:28]
                db.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                           (title, answered_at, conversation_id))
            else:
                db.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                           (answered_at, conversation_id))
        return {
            "user_message": {"id": user_message_id, "role": "user", "content": content,
                             "created_at": asked_at},
            "assistant_message": {"id": assistant_message_id, "role": "assistant",
                                  "content": answer, "created_at": answered_at},
        }

    @api.post("/conversations/{conversation_id}/messages", status_code=201)
    def send_message(conversation_id: str, request: MessageCreate, http_request: Request):
        account = current_account(http_request)
        content = request.content.strip()
        if not content:
            raise HTTPException(400, "请输入问题")
        (
            model_messages, history_ids, material_ids, resource_ids, package, run_id,
            toolbox,
        ) = prepare_turn(conversation_id, account["id"], content)
        act_trace = None
        try:
            prepared = agent.prepare(model_messages, toolbox, enabled=True)
            act_trace = prepared.trace
            answer = model.answer(prepared.messages)
            finish_activity(act_trace)
        except RuntimeError as exc:
            store.complete_perception_run(
                run_id, {"error": str(exc)}, "failed", act_trace
            )
            raise HTTPException(502, str(exc)) from None
        try:
            saved = save_turn(conversation_id, account["id"], content, answer,
                              history_ids, material_ids, resource_ids)
        except RuntimeError as exc:
            store.complete_perception_run(
                run_id, {"error": str(exc)}, "failed", act_trace
            )
            raise HTTPException(409, str(exc)) from None
        store.complete_perception_run(
            run_id, evaluate_answer(answer, package), "completed", act_trace,
            saved["assistant_message"]["id"],
        )
        saved["assistant_message"]["activity"] = public_activities(
            package.trace, act_trace
        )
        return {**saved, "perception_run_id": run_id}

    @api.post("/conversations/{conversation_id}/messages/stream")
    def stream_message(conversation_id: str, request: MessageCreate, http_request: Request):
        account = current_account(http_request)
        content = request.content.strip()
        if not content:
            raise HTTPException(400, "请输入问题")
        (
            model_messages, history_ids, material_ids, resource_ids, package, run_id,
            toolbox,
        ) = prepare_turn(conversation_id, account["id"], content)

        def event(kind: str, **data):
            return json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n"

        def generate():
            parts: list[str] = []
            act_trace = None
            try:
                yield event("progress", activity=perception_activity(package.trace))
                activity_iterator = agent.prepare_events(
                    model_messages, toolbox, enabled=True
                )
                while True:
                    try:
                        activity = next(activity_iterator)
                        yield event("progress", activity=activity)
                    except StopIteration as completed:
                        prepared = completed.value
                        break
                act_trace = prepared.trace
                for delta in model.stream(prepared.messages):
                    if delta:
                        parts.append(delta)
                        yield event("delta", text=delta)
                answer = "".join(parts).strip()
                if not answer:
                    raise RuntimeError("模型没有返回回答")
                finish_activity(act_trace)
                saved = save_turn(conversation_id, account["id"], content, answer,
                                  history_ids, material_ids, resource_ids)
                store.complete_perception_run(
                    run_id, evaluate_answer(answer, package), "completed", act_trace,
                    saved["assistant_message"]["id"],
                )
                saved["assistant_message"]["activity"] = public_activities(
                    package.trace, act_trace
                )
                yield event(
                    "progress",
                    activity=act_trace["activities"][-1],
                )
                yield event("done", **saved, perception_run_id=run_id)
            except Exception as exc:
                finish_activity(act_trace, failed=True)
                if act_trace and act_trace.get("activities"):
                    yield event("progress", activity=act_trace["activities"][-1])
                store.complete_perception_run(
                    run_id, {"error": str(exc)}, "failed", act_trace
                )
                message = str(exc) if isinstance(exc, (RuntimeError, HTTPException)) else "回答中断，请重试"
                yield event("error", message=message)

        return StreamingResponse(
            generate(), media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    return api


app = create_app()
