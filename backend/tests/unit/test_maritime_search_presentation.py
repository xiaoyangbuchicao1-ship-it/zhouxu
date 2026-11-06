import json

import pytest

from backend.contracts.modules.loop import ActionRequest
from backend.infrastructure.search import maritime_corpus
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus, build_index
from backend.infrastructure.tools.maritime import CorpusTool


def test_source_expands_a_law_chunk_to_the_complete_document(tmp_path, monkeypatch):
    law_root = tmp_path / "law-root"
    manifest = law_root / "corpus" / "manifest"
    sources = law_root / "corpus" / "sources"
    manifest.mkdir(parents=True)
    sources.mkdir(parents=True)
    chunk_path = manifest / "chunks.jsonl"
    chunk_path.write_text(
        json.dumps(
            {
                "chunk_id": "law-chunk-1",
                "document_id": "law-document-1",
                "document_name": "中华人民共和国海商法",
                "chunk_text": "第十条　承运人应当妥善保管货物。",
                "is_current": True,
                "version_date": "2025-10-28",
                "article_no": "第十条",
                "canonical_text_path": "corpus/sources/law-document-1.txt",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    complete = "条文ID：law-document-1\n文献来源ID：internal-source\n文献名称：中华人民共和国海商法\n版本日期：2025-10-28\n效力状态：现行有效\n\n中华人民共和国海商法\n第一章　总则\n第十条　承运人应当妥善保管货物。\n第十一条　承运人应当谨慎处理。"
    (sources / "law-document-1.txt").write_text(complete, encoding="utf-8")
    index = tmp_path / "corpus.sqlite3"
    monkeypatch.setitem(maritime_corpus.CORPORA, "laws", chunk_path)
    build_index(index, {"laws": chunk_path})

    source = MaritimeCorpus(index).source("laws", "law-chunk-1")

    assert source["text"].startswith("中华人民共和国海商法")
    assert "条文ID" not in source["text"]
    assert "文献来源ID" not in source["text"]
    assert "版本日期" not in source["text"]
    assert "第十一条" in source["text"]


def test_source_expands_a_case_chunk_to_the_complete_document(tmp_path, monkeypatch):
    case_root = tmp_path / "case-root"
    corpus = case_root / "corpus"
    corpus.mkdir(parents=True)
    chunk_path = corpus / "chunk.jsonl"
    chunk_path.write_text(
        json.dumps(
            {
                "片段编号": "HH0001:片段:001",
                "案例编号": "HH0001",
                "案例标题": "承运人货损案",
                "文本": "案件事实的第一段。",
                "原文相对路径": "raw_materials/case.docx",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    complete = "案件事实的第一段。\n法院认为，承运人应当承担相应责任。"
    (corpus / "cases.jsonl").write_text(
        json.dumps(
            {
                "案例编号": "HH0001",
                "案例身份": {"案例标题": "承运人货损案"},
                "案例全文与来源": {"原文全文": complete},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    index = tmp_path / "corpus.sqlite3"
    monkeypatch.setitem(maritime_corpus.CORPORA, "cases", chunk_path)
    build_index(index, {"cases": chunk_path})

    source = MaritimeCorpus(index).source("cases", "HH0001:片段:001")

    assert source["text"] == complete
    assert "法院认为" in source["text"]


@pytest.mark.asyncio
async def test_search_does_not_publish_backend_fallback_diagnostics(tmp_path, monkeypatch):
    law = tmp_path / "law.jsonl"
    law.write_text(
        json.dumps(
            {
                "chunk_id": "law-1",
                "document_name": "海商法",
                "chunk_text": "承运人承担货物损坏责任。",
                "is_current": True,
                "version_date": "2025-10-28",
                "article_no": "第一条",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    index = tmp_path / "corpus.sqlite3"
    build_index(index, {"laws": law})
    monkeypatch.setattr(
        maritime_corpus,
        "dotenv_values",
        lambda *args, **kwargs: {},
    )

    result = await MaritimeCorpus(index).search("承运人", "laws")

    assert result["items"]
    assert "online_error" not in result
    assert "notices" not in result


@pytest.mark.asyncio
async def test_model_search_result_hides_internal_corpus_metadata():
    class FakeCorpus:
        async def search(self, **_kwargs):
            return {
                "query": "货损",
                "kind": "cases",
                "engine": "local-corpus-fulltext",
                "items": [
                    {
                        "id": "HH0109:片段:013",
                        "kind": "cases",
                        "title": "货损纠纷案",
                        "text": "完整裁判原文。" * 2000,
                        "locator": "原文第13个检索片段",
                        "case_id": "HH0109",
                        "source_path": "raw_materials/internal.docx",
                        "version": None,
                        "citation": "[[ZX:0123456789abcdef]]",
                    }
                ],
            }

    result = await CorpusTool(FakeCorpus()).execute(
        ActionRequest(
            tool_name="search_maritime_corpus",
            arguments={"query": "货损", "kind": "cases"},
        )
    )

    item = result.result["items"][0]
    assert result.result["kind"] == "cases"
    assert "engine" not in result.result
    assert item["title"] == "货损纠纷案"
    assert item["citation"] == "[[ZX:0123456789abcdef]]"
    assert item["excerpt_truncated"] is True
    assert all(
        field not in item
        for field in ("id", "case_id", "source_path", "version", "locator")
    )
