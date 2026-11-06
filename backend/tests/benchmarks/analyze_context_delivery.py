"""Offline evidence audit; reference answers never enter provider requests."""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from docx import Document


def answer_token(text):
    text = re.sub(r"[*`_#]", "", text).strip()
    text = re.sub(r"^(?:(?:答案|正误判断|判断|选择|选)\s*[:：]?\s*)+", "", text)
    match = re.match(r"(错误|不正确|不对|不成立|错|正确|对|[A-Da-d](?![a-zA-Z]))", text)
    if not match:
        return None
    token = match[1]
    return (
        "错"
        if token in {"错误", "不正确", "不对", "不成立", "错"}
        else "对"
        if token in {"正确", "对"}
        else token.upper()
    )


def extract(answer):
    rows, group = [], 1
    for line in (answer or "").splitlines():
        cleaned = re.sub(r"[*`_]", "", line).strip()
        if (cleaned.startswith("#") or line.lstrip().startswith("**")) and not re.match(
            r"^\d", cleaned
        ):
            if "案例" in cleaned or "第二组" in cleaned or "第二部分" in cleaned:
                group = 2
        if cleaned.startswith("|"):
            cells = [c.strip() for c in cleaned.strip("|").split("|")]
            for i in range(len(cells) - 1):
                if re.fullmatch(r"\d{1,2}", cells[i]):
                    token = answer_token(cells[i + 1])
                    if token:
                        rows.append((group, int(cells[i]), token))
            continue
        match = re.match(
            r"^(?:[-]\s*)?(?:第\s*)?(\d{1,2})\s*(?:题)?[.、．:：）)]\s*(.+)", cleaned
        )
        if match and (token := answer_token(match[2])):
            rows.append((group, int(match[1]), token))
    return rows


def score(answer, gold):
    expected = {(1 if i < 87 else 2, n): a for i, (n, a) in enumerate(gold)}
    parsed = extract(answer)
    observed = defaultdict(list)
    for group, number, token in parsed:
        observed[(group, number)].append(token)
    conflicts = {str(k): v for k, v in observed.items() if len(set(v)) > 1}
    aligned = set(expected) == set(observed) and not conflicts
    wrong = [
        {"group": k[0], "question": k[1], "expected": v, "actual": observed[k][0]}
        for k, v in expected.items()
        if k in observed and observed[k][0] != v
    ]
    return {
        "aligned": aligned,
        "extracted": len(parsed),
        "expected": len(expected),
        "correct_leading_options": len(expected) - len(wrong) if aligned else None,
        "order_preserved": [(g, n) for g, n, _ in parsed] == list(expected),
        "conflicts": conflicts,
        "missing": [list(k) for k in expected if k not in observed],
        "wrong_leading_options": wrong,
        "explanation_correctness": "requires_human_review; leading option is not proof",
    }


def analyze(report, reference):
    output = []
    for row in report["rows"]:
        calls = row["calls"]
        actions = [t["tool_name"] for t in row["tools"]]
        scored = (
            score(row["answer"], reference[:10] if row["case"] == "P10" else reference)
            if row["case"] in {"P10", "P97"}
            else None
        )
        output.append(
            {
                "case": row["case"],
                "turn": row["turn"],
                "run_id": row["run_id"],
                "status": row["status"],
                "error": row["error"],
                "seconds": row["seconds"],
                "model_calls": len(calls),
                "tools": actions,
                "duplicate_read_flags": sum(
                    len(c.get("potential_duplicate_reads", [])) for c in calls
                ),
                "all_within_budget": bool(calls)
                and all(
                    c.get("wire_tokens_estimate", 0) <= c.get("budget_warning", -1)
                    for c in calls
                ),
                "http_statuses": [c.get("http_status") for c in calls],
                "protocols": [c.get("protocol") for c in calls],
                "compactions": sum(c["purpose"] == "compaction" for c in calls),
                "compacted_records": row["compacted_record_ids"],
                "stream_matches_answer": row["stream_matches_answer"],
                "delta_count": row["delta_count"],
                "internal_markers": bool(
                    re.search(
                        r"#chunk-|next_cursor|parse_version|question-source",
                        row["answer"] or "",
                    )
                ),
                "machine_checks": row.get("checks"),
            "missing_usage_calls": sum(c.get("usage") is None for c in calls),
                "reference_score": scored,
                "input_tokens": sum(
                    (c.get("usage") or {}).get("prompt_tokens", 0) for c in calls
                ),
                "output_tokens": sum(
                    (c.get("usage") or {}).get("completion_tokens", 0) for c in calls
                ),
                "reasoning_tokens": sum(
                    ((c.get("usage") or {}).get("completion_tokens_details") or {}).get(
                        "reasoning_tokens", 0
                    )
                    for c in calls
                ),
            }
        )
    return output


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    text = "\n".join(p.text for p in Document(args.reference).paragraphs)
    gold = [
        (int(n), a.upper())
        for n, a in re.findall(r"(\d+)\s*[.．]?\s*(对|错|[A-Da-d])", text)
    ]
    if len(gold) != 97 or gold[86][0] != 87 or gold[87][0] != 1:
        raise ValueError("reference group layout needs human review")
    result = {
        "reference_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
        "reference_is_not_current_law_authority": True,
        "reports": {
            p.name: analyze(json.loads(p.read_text(encoding="utf-8")), gold)
            for p in args.reports
        },
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, rows in result["reports"].items():
        print(
            name,
            json.dumps(
                [
                    {
                        k: r[k]
                        for k in (
                            "case",
                            "turn",
                            "seconds",
                            "model_calls",
                            "duplicate_read_flags",
                            "reference_score",
                        )
                    }
                    for r in rows
                ],
                ensure_ascii=False,
            ),
        )


if __name__ == "__main__":
    main()
