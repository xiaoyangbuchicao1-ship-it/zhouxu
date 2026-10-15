"""账号级资源库。

资源库资料与案件附件是两类对象：前者可跨对话复用，后者只属于一段案件对话。
资源只有显式关联到对话后才会进入该对话的检索范围。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from array import array
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from materials import extract_material
from perception_layer.chunking import build_content_units
from perception_layer.contracts import ContentUnit
from perception_layer.retrieval import NoSemanticEncoder, SemanticEncoder


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class ResourceLibrary:
    """管理资料生命周期；不负责决定模型最终采用哪些资料。"""

    def __init__(
        self,
        database: Callable[[], AbstractContextManager[sqlite3.Connection]],
        root: Path,
        clock: Callable[[], str],
        encoder: SemanticEncoder | None = None,
    ):
        self.database = database
        self.root = root / "resources"
        self.clock = clock
        self.encoder = encoder or NoSemanticEncoder()
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resource_documents(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT,
                    stored_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    extracted_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    issue TEXT,
                    parser_name TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    UNIQUE(owner_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS resource_chunks(
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    locator TEXT NOT NULL,
                    heading_path_json TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    boundary_reason TEXT NOT NULL,
                    sentence_complete INTEGER NOT NULL,
                    previous_id TEXT,
                    next_id TEXT,
                    embedding_model TEXT,
                    embedding_blob BLOB,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(document_id) REFERENCES resource_documents(id) ON DELETE CASCADE,
                    UNIQUE(document_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS conversation_resources(
                    conversation_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, document_id),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                    FOREIGN KEY(document_id) REFERENCES resource_documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_resource_documents_owner
                    ON resource_documents(owner_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_resource_chunks_document
                    ON resource_chunks(document_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_conversation_resources_document
                    ON conversation_resources(document_id, conversation_id);
            """)
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(resource_chunks)")
            }
            if "embedding_model" not in columns:
                db.execute("ALTER TABLE resource_chunks ADD COLUMN embedding_model TEXT")
            if "embedding_blob" not in columns:
                db.execute("ALTER TABLE resource_chunks ADD COLUMN embedding_blob BLOB")

    @staticmethod
    def _public_document(row: sqlite3.Row | dict) -> dict:
        value = dict(row)
        value.pop("stored_path", None)
        value.pop("extracted_text", None)
        return value

    def list(self, owner_id: str) -> list[dict]:
        with self.database() as db:
            rows = db.execute("""
                SELECT d.id,d.filename,d.content_type,d.sha256,d.status,d.issue,
                       d.parser_name,d.parser_version,d.chunker_version,
                       d.created_at,d.updated_at,
                       COUNT(DISTINCT c.id) AS chunk_count,
                       COUNT(DISTINCT cr.conversation_id) AS conversation_count
                FROM resource_documents d
                LEFT JOIN resource_chunks c ON c.document_id=d.id
                LEFT JOIN conversation_resources cr ON cr.document_id=d.id
                WHERE d.owner_id=?
                GROUP BY d.id
                ORDER BY d.updated_at DESC,d.id DESC
            """, (owner_id,)).fetchall()
        return [self._public_document(row) for row in rows]

    def upload(
        self,
        owner_id: str,
        filename: str,
        content_type: str | None,
        payload: bytes,
    ) -> dict:
        safe_name = Path(filename).name
        digest = hashlib.sha256(payload).hexdigest()
        with self.database() as db:
            existing = db.execute(
                "SELECT * FROM resource_documents WHERE owner_id=? AND sha256=?",
                (owner_id, digest),
            ).fetchone()
            if existing:
                result = self._public_document(existing)
                result["chunk_count"] = db.execute(
                    "SELECT COUNT(*) FROM resource_chunks WHERE document_id=? AND enabled=1",
                    (existing["id"],),
                ).fetchone()[0]
                return result

        document_id = _new_id("resource")
        folder = self.root / owner_id
        folder.mkdir(parents=True, exist_ok=True)
        stored = folder / f"{document_id}{Path(safe_name).suffix.lower()}"
        stored.write_bytes(payload)
        extraction = extract_material(safe_name, payload, stored)
        material = {
            "id": document_id,
            "filename": safe_name,
            "extracted_text": extraction.text,
            "message_id": "resource-library",
            "source_scope": "knowledge_resource",
        }
        units = build_content_units(material)
        embeddings: dict[str, bytes] = {}
        if units and not isinstance(self.encoder, NoSemanticEncoder):
            try:
                vectors = self.encoder.encode([unit.search_text for unit in units])
                embeddings = {
                    unit.id: array("f", vector).tobytes()
                    for unit, vector in zip(units, vectors)
                }
            except Exception:
                # 语义索引不可用时仍保留 BM25 检索，不能让用户丢失已上传资料。
                embeddings = {}
        timestamp = self.clock()
        status = "indexed" if units else extraction.status
        issue = extraction.issue or (None if units else "没有生成可检索内容")
        try:
            with self.database() as db:
                db.execute("""
                    INSERT INTO resource_documents(
                        id,owner_id,filename,content_type,stored_path,sha256,extracted_text,
                        status,issue,parser_name,parser_version,chunker_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    document_id, owner_id, safe_name, content_type, str(stored), digest,
                    extraction.text, status, issue, extraction.parser_name,
                    extraction.parser_version, "structure-v2", timestamp, timestamp,
                ))
                db.executemany("""
                    INSERT INTO resource_chunks(
                        id,document_id,ordinal,locator,heading_path_json,kind,text,search_text,
                        estimated_tokens,boundary_reason,sentence_complete,previous_id,next_id,
                        embedding_model,embedding_blob,enabled
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                """, [
                    (
                        unit.id, document_id, unit.ordinal, unit.locator,
                        json.dumps(list(unit.heading_path), ensure_ascii=False), unit.kind,
                        unit.text, unit.search_text, unit.estimated_tokens,
                        unit.boundary_reason, int(unit.sentence_complete),
                        unit.previous_id, unit.next_id,
                        self.encoder.name if unit.id in embeddings else None,
                        embeddings.get(unit.id),
                    )
                    for unit in units
                ])
        except Exception:
            stored.unlink(missing_ok=True)
            raise
        return {
            "id": document_id,
            "filename": safe_name,
            "content_type": content_type,
            "sha256": digest,
            "status": status,
            "issue": issue,
            "parser_name": extraction.parser_name,
            "parser_version": extraction.parser_version,
            "chunker_version": "structure-v2",
            "chunk_count": len(units),
            "conversation_count": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def delete(self, owner_id: str, document_id: str) -> None:
        with self.database() as db:
            row = db.execute(
                "SELECT stored_path FROM resource_documents WHERE id=? AND owner_id=?",
                (document_id, owner_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "没有找到这份资源")
            db.execute("DELETE FROM resource_documents WHERE id=?", (document_id,))
        Path(row["stored_path"]).unlink(missing_ok=True)

    def chunks(self, owner_id: str, document_id: str) -> list[dict]:
        with self.database() as db:
            owner = db.execute(
                "SELECT 1 FROM resource_documents WHERE id=? AND owner_id=?",
                (document_id, owner_id),
            ).fetchone()
            if not owner:
                raise HTTPException(404, "没有找到这份资源")
            rows = db.execute("""
                SELECT id,ordinal,locator,heading_path_json,kind,text,estimated_tokens,
                       boundary_reason,sentence_complete,enabled
                FROM resource_chunks WHERE document_id=? ORDER BY ordinal
            """, (document_id,)).fetchall()
        return [
            {
                **dict(row),
                "heading_path": json.loads(row["heading_path_json"]),
                "sentence_complete": bool(row["sentence_complete"]),
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]

    def link(self, owner_id: str, conversation_id: str, document_id: str) -> None:
        with self.database() as db:
            conversation = db.execute(
                "SELECT 1 FROM conversations WHERE id=? AND owner_id=?",
                (conversation_id, owner_id),
            ).fetchone()
            document = db.execute(
                "SELECT 1 FROM resource_documents WHERE id=? AND owner_id=?",
                (document_id, owner_id),
            ).fetchone()
            if not conversation:
                raise HTTPException(404, "没有找到这段对话")
            if not document:
                raise HTTPException(404, "没有找到这份资源")
            db.execute("""
                INSERT OR IGNORE INTO conversation_resources(conversation_id,document_id,linked_at)
                VALUES(?,?,?)
            """, (conversation_id, document_id, self.clock()))

    def unlink(self, owner_id: str, conversation_id: str, document_id: str) -> None:
        with self.database() as db:
            conversation = db.execute(
                "SELECT 1 FROM conversations WHERE id=? AND owner_id=?",
                (conversation_id, owner_id),
            ).fetchone()
            if not conversation:
                raise HTTPException(404, "没有找到这段对话")
            db.execute(
                "DELETE FROM conversation_resources WHERE conversation_id=? AND document_id=?",
                (conversation_id, document_id),
            )

    def linked_documents(self, owner_id: str, conversation_id: str) -> list[dict]:
        with self.database() as db:
            rows = db.execute("""
                SELECT d.id,d.filename,d.status,d.issue,d.parser_name,d.parser_version,
                       d.chunker_version,d.created_at,d.updated_at,
                       COUNT(c.id) AS chunk_count
                FROM conversation_resources cr
                JOIN conversations conv ON conv.id=cr.conversation_id AND conv.owner_id=?
                JOIN resource_documents d ON d.id=cr.document_id AND d.owner_id=?
                LEFT JOIN resource_chunks c ON c.document_id=d.id AND c.enabled=1
                WHERE cr.conversation_id=?
                GROUP BY d.id
                ORDER BY cr.linked_at,d.id
            """, (owner_id, owner_id, conversation_id)).fetchall()
        return [dict(row) for row in rows]

    def linked_units(
        self, owner_id: str, conversation_id: str
    ) -> tuple[list[dict], list[ContentUnit]]:
        documents = self.linked_documents(owner_id, conversation_id)
        if not documents:
            return [], []
        with self.database() as db:
            rows = db.execute("""
                SELECT c.*,d.filename FROM conversation_resources cr
                JOIN resource_documents d ON d.id=cr.document_id AND d.owner_id=?
                JOIN resource_chunks c ON c.document_id=d.id AND c.enabled=1
                WHERE cr.conversation_id=?
                ORDER BY d.filename,c.ordinal
            """, (owner_id, conversation_id)).fetchall()
        units = [
            ContentUnit(
                id=row["id"],
                material_id=row["document_id"],
                filename=row["filename"],
                ordinal=row["ordinal"],
                locator=row["locator"],
                heading_path=tuple(json.loads(row["heading_path_json"])),
                kind=row["kind"],
                text=row["text"],
                search_text=row["search_text"],
                estimated_tokens=row["estimated_tokens"],
                boundary_reason=row["boundary_reason"],
                sentence_complete=bool(row["sentence_complete"]),
                previous_id=row["previous_id"],
                next_id=row["next_id"],
                current_upload=False,
                source_scope="knowledge_resource",
                embedding=self._decode_embedding(row),
            )
            for row in rows
        ]
        return documents, units

    def linked_workspace_files(self, owner_id: str, conversation_id: str) -> list[dict]:
        """Return private file paths only to the local ACT executor."""
        with self.database() as db:
            rows = db.execute("""
                SELECT d.id,d.filename,d.stored_path
                FROM conversation_resources cr
                JOIN conversations conv ON conv.id=cr.conversation_id AND conv.owner_id=?
                JOIN resource_documents d ON d.id=cr.document_id AND d.owner_id=?
                WHERE cr.conversation_id=?
                ORDER BY cr.linked_at,d.id
            """, (owner_id, owner_id, conversation_id)).fetchall()
        return [dict(row) for row in rows]

    def _decode_embedding(self, row: sqlite3.Row) -> tuple[float, ...] | None:
        if row["embedding_model"] != self.encoder.name or not row["embedding_blob"]:
            return None
        values = array("f")
        values.frombytes(row["embedding_blob"])
        return tuple(values)
