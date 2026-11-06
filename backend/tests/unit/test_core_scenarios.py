"""Prove that the reusable oracles catch wrong results, not just happy paths."""

import copy
import json

import pytest

from backend.infrastructure.model.reading import reference_view
from backend.tests.scenarios.run_core import (
    CORPUS,
    check_result,
    fixture_text,
    write_fixtures,
)


def test_corpus_is_portable_and_has_explicit_review_oracles():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 32
    assert len({c["family"] for c in cases}) == 7
    assert sum(c["tier"] == "challenge" for c in cases) == 8
    for case in cases:
        assert case["risks"] and case["turns"]
        assert all(key in corpus["fixtures"] for key in case.get("materials", []))
        for turn in case["turns"]:
            assert turn["text"].strip() and turn["rubric"].strip()
            assert turn["expected"]["status"] in {"completed", "needs_input"}
    # Facts are located away from the initial preview, with a real continuation.
    text = fixture_text(corpus["fixtures"]["long"])
    assert text.index("潮汐-462") > 6000 and text.index("银帆八六二") > 20000


def test_fixture_formats_retain_text_and_original_question_order(tmp_path):
    from docx import Document

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    paths = write_fixtures(corpus, tmp_path)
    actual = [p.text for p in Document(paths["questions"]).paragraphs]
    assert actual == corpus["fixtures"]["questions"]["paragraphs"]
    assert paths["ledger"].read_text(encoding="utf-8").splitlines()[1] == "ZX-101,80,2"


@pytest.mark.parametrize(
    "mutation,expected_failure",
    [
        ({"status": "completed"}, "wrong_terminal_state"),
        ({"answer": ""}, "missing_expected_fact"),
        ({"error": "timeout"}, "run_error:timeout"),
        ({"stream_matches_answer": False}, "stream_final_mismatch"),
        ({"public_error_events": 1}, "public_error_event"),
        ({"answer": "82 [a#chunk-0001]"}, "internal_metadata_in_answer"),
        ({"calls": [{"within_budget": False}]}, "request_over_budget"),
    ],
)
def test_oracles_reject_adversarial_outcomes(mutation, expected_failure):
    row = {
        "status": "needs_input",
        "answer": "82",
        "tools": [],
        "calls": [],
        "error": None,
        "stream_matches_answer": True,
    }
    row.update(mutation)
    checks = check_result({"status": "needs_input", "contains": ["82"]}, row)
    assert not checks["machine_pass"] and expected_failure in checks["failures"]


def test_retrieval_oracle_requires_body_not_just_a_successful_tool_name():
    row = {
        "status": "completed",
        "answer": "未取得正文",
        "tools": [{"tool_name": "dynamic_rag_search", "result": {"items": []}}],
        "calls": [],
        "stream_matches_answer": True,
    }
    assert (
        "no_retrieved_body"
        in check_result({"status": "completed", "evidence_required": True}, row)[
            "failures"
        ]
    )
    assert (
        "unnecessary_tool"
        in check_result(
            {"status": "completed", "forbid_tools": ["dynamic_rag_search"]}, row
        )["failures"]
    )
    assert (
        "required_observation_not_obtained"
        in check_result(
            {"status": "completed", "require_any_tool": ["read_material"]}, row
        )["failures"]
    )


@pytest.mark.parametrize("answer", ["1. A\n2. B", "1. C\n2. B\n3. D", "2. B\n1. C"])
def test_answer_oracle_catches_wrong_option_extra_item_and_order(answer):
    row = {
        "status": "completed",
        "answer": answer,
        "tools": [],
        "calls": [],
        "stream_matches_answer": True,
    }
    assert (
        "answer_key_or_order_mismatch"
        in check_result({"status": "completed", "answer_key": ["C", "B"]}, row)[
            "failures"
        ]
    )


def test_unrecognized_answer_format_requires_review_instead_of_invented_score():
    row = {
        "status": "completed",
        "answer": "选项见表格",
        "tools": [],
        "calls": [],
        "stream_matches_answer": True,
    }
    result = check_result({"status": "completed", "answer_key": ["C", "B"]}, row)
    assert result["manual_checks"] == ["answer_format_needs_human_check"]
    assert result["subjective_review_required"]


@pytest.mark.parametrize("count", [0, 1, 100, 4000])
def test_reference_projection_does_not_mutate_records_or_infer_contiguous_coverage(
    count,
):
    original = {
        "updates": {
            "summary": {
                "material_id": "fixture-many",
                "status": "partial",
                "parse_version": "v1",
                "next_cursor": "real-cursor",
                "unit_ids": list(range(count)),
                "coverage": [
                    {"unit_id": n * 2, "char_start": 3, "char_end": 6}
                    for n in range(count)
                ],
            }
        }
    }
    before = copy.deepcopy(original)
    view = reference_view(original)["updates"]["summary"]
    assert original == before
    assert view["coverage_preview"]["count"] == count
    assert view["unit_ids_preview"]["count"] == count
    assert view["status"] == "partial" and view["next_cursor"] == "real-cursor"
    assert len(json.dumps(view)) < 700
    assert "coverage" not in view and "unit_ids" not in view


def test_non_material_reference_is_not_reinterpreted():
    value = {"query": "example", "coverage": [1, 2, 3], "status": "failed"}
    assert reference_view(value) == value


@pytest.mark.parametrize("status,error,questions,passed", [
    ("needs_input", "missing_input", ["请提供原始凭证。"], True),
    ("needs_input", None, ["请提供原始凭证。"], True),
    ("needs_input", "missing_input", [], False),
    ("needs_input", "timeout", ["请提供原始凭证。"], False),
    ("completed", "missing_input", ["请提供原始凭证。"], False),
])
def test_waiting_marker_is_accepted_only_with_correct_state_and_real_questions(status, error, questions, passed):
    row = {"status": status, "error": error, "missing_inputs": questions,
           "answer": None, "calls": [], "tools": [], "stream_matches_answer": True}
    assert check_result({"status": "needs_input"}, row)["machine_pass"] is passed


def test_professional_fixture_is_local_and_its_key_is_evaluation_only(tmp_path):
    corpus = json.loads(
        CORPUS.with_name("professional_v1.json").read_text(encoding="utf-8")
    )
    paths = write_fixtures(corpus, tmp_path)
    assert (
        paths["questions"].read_bytes()
        == (CORPUS.parent / corpus["fixtures"]["questions"]["file"]).read_bytes()
    )
    turn = corpus["cases"][0]["turns"][0]
    assert "judgment_key" not in turn["text"]
    row = {
        "status": "completed",
        "answer": "1. 错误。\n2. C。",
        "tools": [],
        "calls": [],
        "stream_matches_answer": True,
    }
    result = check_result({"status": "completed", "judgment_key": ["对", "C"]}, row)
    assert "user_reference_key_mismatch" in result["failures"]
    assert result["subjective_review_required"]
