import io
import zipfile

import pytest

from backend.infrastructure.execution.runtime import ExecutionStore, validate_artifact


def test_office_delivery_checks_real_container_parts_and_rejects_renamed_text():
    with pytest.raises(ValueError):
        validate_artifact("fake.docx", b"not an office package")
    with pytest.raises(ValueError):
        validate_artifact("empty.txt", b"")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr(
            "word/document.xml",
            '<document xmlns="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
    validate_artifact("draft.docx", buffer.getvalue())
    with pytest.raises(ValueError):
        validate_artifact("wrong.xlsx", buffer.getvalue())


def test_delivery_quota_and_snapshot_are_run_scoped(tmp_path):
    store = ExecutionStore(tmp_path)
    for _ in range(10):
        store.save("one", "result.txt", b"verified")
    with pytest.raises(ValueError, match="quota"):
        store.save("one", "excess.txt", b"excess")
    item = store.save("two", "result.txt", b"other")
    assert store.get("one", item["id"]) is None
    assert store.get("two", item["id"])[1] == b"other"
