from backend.tests.benchmarks.analyze_context_delivery import extract, score


def test_reference_groups_do_not_mix_restarted_numbers():
    answer = "## 第一部分\n1. 对\n87. B\n## 第二部分（案例选择题）\n1. D\n"
    assert extract(answer) == [(1, 1, "对"), (1, 87, "B"), (2, 1, "D")]


def test_reordered_answers_match_identity_but_report_order_change():
    result = score("2. B\n1. 对", [(1, "对"), (2, "B")])
    assert result["aligned"] and result["correct_leading_options"] == 2
    assert not result["order_preserved"]


def test_conflicting_or_missing_options_cannot_receive_a_score():
    assert score("1. 对\n1. 错", [(1, "对")])["correct_leading_options"] is None
    assert score("1. 对", [(1, "对"), (2, "A")])["correct_leading_options"] is None
