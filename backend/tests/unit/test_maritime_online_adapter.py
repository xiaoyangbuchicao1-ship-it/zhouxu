import httpx
import pytest

from backend.infrastructure.search import maritime_corpus
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus


@pytest.mark.asyncio
async def test_case_search_targets_deployed_alias_and_normalizes_online_schema(
    tmp_path, monkeypatch
):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            source = {
                "chunk_id": "HH0001:片段:001",
                "case_id": "HH0001",
                "title": "水上运输案",
                "text": "运输事故争议原文",
                "start": 10,
                "end": 32,
                "snapshot_id": "original-snapshot",
            }
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"hits": {"hits": [{"_source": source}]}},
            )

    monkeypatch.setattr(maritime_corpus.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        maritime_corpus,
        "dotenv_values",
        lambda *a, **k: {"OPENSEARCH_INITIAL_ADMIN_PASSWORD": "fixture-only"},
    )
    corpus = MaritimeCorpus(tmp_path / "absent.db")
    result = await corpus.search("运输", "cases")
    assert result["engine"] == "opensearch-bm25"
    assert calls[0][0] == "https://127.0.0.1:19200/maritime_cases_read/_search"
    assert calls[0][1]["query"]["bool"]["must"][0]["multi_match"]["fields"] == [
        "title^4",
        "text",
    ]
    item = result["items"][0]
    assert item["id"] == "HH0001:片段:001" and item["case_id"] == "HH0001"
    assert corpus.source("cases", item["id"])["text"] == "运输事故争议原文"
