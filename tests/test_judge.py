from eval_harness.cases import parse_case
from eval_harness.judge import run_checks, weighted_score
from eval_harness.types import CriterionScore, Judgment


def sample_case():
    return parse_case(
        {
            "id": "checks",
            "input": "Prompt",
            "rubric": [
                {"id": "a", "description": "A", "weight": 3},
                {"id": "b", "description": "B", "weight": 1},
            ],
            "checks": {
                "must_include": ["required"],
                "must_not_include": ["forbidden"],
                "max_chars": 30,
            },
        },
        1,
    )


def test_deterministic_checks_are_case_insensitive():
    result = run_checks(sample_case(), "The REQUIRED thing.")
    assert result.passed


def test_deterministic_checks_report_all_failures():
    result = run_checks(sample_case(), "forbidden " * 10)
    assert not result.passed
    assert len(result.details) == 3


def test_weighted_score_normalizes_to_100():
    judgment = Judgment(
        scores=[
            CriterionScore("a", 4, "good"),
            CriterionScore("b", 0, "bad"),
        ],
        overall_reason="",
        judge="test",
    )
    assert weighted_score(sample_case(), judgment) == 75.0

