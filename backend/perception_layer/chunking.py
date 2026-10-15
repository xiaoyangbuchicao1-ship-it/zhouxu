"""把解析后的文档按原有结构切成可定位的内容单元。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass

from .contracts import ContentUnit, estimate_tokens


_SENTENCE_END = re.compile(r"[。！？!?；;：:]$|[）)】\]》”’\"']$")
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_CONTRACT_HEADING = re.compile(r"^第[一二三四五六七八九十百零〇两\d]+条(?:\s|[：:、.]|$)")
_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+){0,3}|[一二三四五六七八九十]+)[、.．]\s*\S+")
_TABLE_ROW = re.compile(r"^第\s*\d+\s*行[：:]")
_EMAIL_HEADER = re.compile(
    r"^(?:From|To|Cc|Bcc|Date|Subject|发件人|收件人|抄送|日期|主题)[：:]", re.I
)


def _natural_boundary(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped or _TABLE_ROW.match(stripped) or _EMAIL_HEADER.match(stripped):
        return True
    return bool(_SENTENCE_END.search(stripped))


def _sentences(text: str) -> list[str]:
    compact = re.sub(
        r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")
    ).strip()
    if not compact:
        return []
    return [
        piece.strip()
        for piece in re.split(r"(?<=[。！？!?；;])\s*|\n+", compact)
        if piece.strip()
    ]


@dataclass
class _Block:
    text: str
    locator: str
    heading_path: tuple[str, ...]
    kind: str
    boundary_reason: str


def _classify_line(line: str) -> str:
    if _TABLE_ROW.match(line):
        return "table_row"
    if _EMAIL_HEADER.match(line):
        return "email_header"
    if _CONTRACT_HEADING.match(line):
        return "contract_clause"
    return "text"


def _structural_blocks(text: str) -> list[_Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings: list[str] = []
    current_locator = "正文"
    blocks: list[_Block] = []
    paragraph: list[str] = []
    paragraph_kind = "text"

    def flush(reason: str = "paragraph_boundary") -> None:
        nonlocal paragraph, paragraph_kind
        value = "\n".join(item for item in paragraph if item.strip()).strip()
        if value:
            blocks.append(_Block(value, current_locator, tuple(headings), paragraph_kind, reason))
        paragraph = []
        paragraph_kind = "text"

    for raw in lines:
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            flush()
            continue
        heading = _MARKDOWN_HEADING.match(line)
        if heading:
            flush("heading_boundary")
            level = len(heading.group(1))
            title = heading.group(2).strip()
            headings[:] = headings[: max(0, level - 1)]
            headings.append(title)
            current_locator = title
            continue
        if _CONTRACT_HEADING.match(line) or (_NUMBERED_HEADING.match(line) and len(line) <= 100):
            flush("heading_boundary")
            current_locator = line[:100]
            if headings:
                headings[-1:] = [line[:100]]
            else:
                headings.append(line[:100])
            paragraph = [line]
            paragraph_kind = "contract_clause"
            continue
        kind = _classify_line(line)
        if kind in {"table_row", "email_header"}:
            if paragraph and paragraph_kind != kind:
                flush("structure_change")
            paragraph_kind = kind
            paragraph.append(line)
            continue
        if paragraph_kind in {"table_row", "email_header"}:
            flush("structure_change")
        paragraph_kind = "text" if paragraph_kind == "text" else paragraph_kind
        paragraph.append(line)
    flush("document_end")
    return blocks


def _split_oversized(block: _Block, max_tokens: int) -> list[_Block]:
    if estimate_tokens(block.text) <= max_tokens:
        return [block]
    if block.kind == "table_row":
        rows = [line.strip() for line in block.text.splitlines() if line.strip()]
        header, data_rows = rows[0], rows[1:]
        output: list[_Block] = []
        buffer = [header]
        for row in data_rows:
            candidate = "\n".join([*buffer, row])
            if len(buffer) > 1 and estimate_tokens(candidate) > max_tokens:
                output.append(
                    _Block(
                        "\n".join(buffer), block.locator, block.heading_path,
                        block.kind, "table_row_boundary"
                    )
                )
                buffer = [header, row]
            elif len(buffer) == 1 and estimate_tokens(candidate) > max_tokens:
                output.append(
                    _Block(row, block.locator, block.heading_path, block.kind, "hard_limit")
                )
                buffer = [header]
            else:
                buffer.append(row)
        if len(buffer) > 1 or not output:
            output.append(
                _Block(
                    "\n".join(buffer), block.locator, block.heading_path,
                    block.kind, "table_row_boundary"
                )
            )
        return output
    if block.kind in {"table_row", "email_header"}:
        sentences = [line.strip() for line in block.text.splitlines() if line.strip()]
        reason = "table_row_boundary" if block.kind == "table_row" else "structure_change"
    else:
        sentences = _sentences(block.text)
        reason = "sentence_boundary"
    if len(sentences) <= 1:
        pieces = re.split(r"(?<=[，、,])", block.text)
        if len(pieces) <= 1:
            chars = max(240, int(max_tokens * 1.35))
            pieces = [block.text[index : index + chars] for index in range(0, len(block.text), chars)]
        reason = "hard_limit"
    else:
        pieces = sentences
    output: list[_Block] = []
    buffer: list[str] = []
    for piece in pieces:
        candidate = "".join(buffer + [piece]) if reason == "hard_limit" else "\n".join(buffer + [piece])
        if buffer and estimate_tokens(candidate) > max_tokens:
            output.append(_Block("\n".join(buffer), block.locator, block.heading_path, block.kind, reason))
            buffer = [piece]
        else:
            buffer.append(piece)
    if buffer:
        output.append(_Block("\n".join(buffer), block.locator, block.heading_path, block.kind, reason))
    return output


def build_content_units(
    material: dict, *, max_tokens: int = 720, merge_target_tokens: int = 520
) -> list[ContentUnit]:
    """结构优先，仅在单个结构块过大时才按自然句界继续切。"""
    text = material.get("extracted_text") or ""
    if not text.strip():
        return []
    raw_blocks = [
        part
        for block in _structural_blocks(text)
        for part in _split_oversized(block, max_tokens)
    ]
    merged: list[_Block] = []
    for block in raw_blocks:
        if not merged:
            merged.append(block)
            continue
        previous = merged[-1]
        same_scope = previous.heading_path == block.heading_path and previous.locator == block.locator
        compatible = previous.kind == block.kind or {previous.kind, block.kind} <= {
            "text",
            "contract_clause",
        }
        candidate = previous.text + "\n" + block.text
        if same_scope and compatible and estimate_tokens(candidate) <= merge_target_tokens:
            merged[-1] = _Block(
                candidate, previous.locator, previous.heading_path, previous.kind, "merged_same_scope"
            )
        else:
            merged.append(block)

    provisional: list[ContentUnit] = []
    for ordinal, block in enumerate(merged, start=1):
        heading_prefix = " > ".join(block.heading_path)
        search_text = f"{heading_prefix}\n{block.text}".strip()
        digest = hashlib.sha256(
            f"{material['id']}:{ordinal}:{search_text}".encode("utf-8")
        ).hexdigest()[:20]
        provisional.append(
            ContentUnit(
                id=f"unit-{digest}",
                material_id=material["id"],
                filename=material["filename"],
                ordinal=ordinal,
                locator=block.locator,
                heading_path=block.heading_path,
                kind=block.kind,
                text=block.text,
                search_text=search_text,
                estimated_tokens=estimate_tokens(search_text),
                boundary_reason=block.boundary_reason,
                sentence_complete=_natural_boundary(block.text),
                current_upload=material.get("message_id") is None,
                source_scope=material.get("source_scope", "case_material"),
            )
        )
    return [
        ContentUnit(
            **{
                **asdict(unit),
                "previous_id": provisional[index - 1].id if index else None,
                "next_id": provisional[index + 1].id if index + 1 < len(provisional) else None,
            }
        )
        for index, unit in enumerate(provisional)
    ]
