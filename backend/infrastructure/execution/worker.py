"""Runs INSIDE the isolated execution world, never on the host."""

import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path("/workspace")
LIMIT = 10 * 1024 * 1024


def target(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("invalid_workspace_path")
    raw = Path(name)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("workspace_relative_path_required")
    current = ROOT
    for part in raw.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink_not_allowed")
    resolved = current.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("outside_workspace")
    return resolved


def content(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
            raise ValueError("regular_single_link_file_under_10MiB_required")
        value = handle.read(LIMIT + 1)
        if len(value) > LIMIT:
            raise ValueError("file_too_large")
        return value


def main(value):
    path = target(value.get("path", "."))
    op = value["op"]
    if op == "read" and path.is_dir():
        return {
            "entries": sorted(
                p.name + ("/" if p.is_dir() else "") for p in path.iterdir()
            )[:200]
        }
    if op in ("read", "export"):
        data = content(path)
        result = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        if op == "export":
            result["data"] = base64.b64encode(data).decode()
        else:
            text = data.decode("utf-8")
            start = max(0, int(value.get("offset", 0)))
            result.update(
                text=text[start : start + 20000],
                offset=start,
                truncated=len(text) > start + 20000,
            )
        return result
    if op not in ("write", "edit"):
        raise ValueError("unknown_operation")
    old = content(path) if path.exists() else None
    expected = value.get("expected_sha256")
    if old is not None and expected != hashlib.sha256(old).hexdigest():
        raise ValueError("file_changed_read_current_sha256_before_editing")
    if op == "edit":
        if old is None:
            raise ValueError("file_not_found")
        source, replacement = value["old_text"], value["new_text"]
        text = old.decode("utf-8")
        if not source or text.count(source) != 1:
            raise ValueError("old_text_must_match_exactly_once")
        data = text.replace(source, replacement, 1).encode()
    else:
        data = value["content"].encode("utf-8")
    if len(data) > 200000:
        raise ValueError("write_limit_200000_bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


try:
    print(json.dumps({"ok": True, **main(json.load(sys.stdin))}, ensure_ascii=False))
except (ValueError, OSError, UnicodeError, KeyError, TypeError) as exc:
    print(json.dumps({"ok": False, "error": str(exc)[:300]}, ensure_ascii=False))
