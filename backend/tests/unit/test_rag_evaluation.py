import pytest

pytest.importorskip("numpy", reason="Benchmark utilities require the rag extra")

from backend.tests.benchmarks.run_rag import citation_metrics


def test_reference_identity_is_separate_from_formatting():
    real = "b56b1a10f518be90#chunk-0001"
    fake = "ffffffffffffffff#chunk-9999"
    result = citation_metrics(f"[{real}] [chunk_id: {real}] {real} [{fake}]", {real})
    assert result["citation_ids"] == [real, real, real, fake]
    assert result["invalid_citation_ids"] == [fake]
    assert result["noncanonical_citation_count"] == 2


def test_absence_of_citations_is_not_a_validity_success_claim():
    result = citation_metrics("No evidence was supplied", set())
    assert result["citation_ids"] == []
    assert result["invalid_citation_ids"] == []
