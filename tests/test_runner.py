import json

from eval_harness.cases import parse_case
from eval_harness.models import create_adapter
from eval_harness.runner import run_evaluation


def test_end_to_end_mock_run_writes_artifacts(tmp_path):
    case = parse_case(
        {
            "id": "refund-delay",
            "input": "I returned my headphones. Where is my money?",
            "rubric": [{"id": "useful", "description": "Is useful."}],
            "checks": {"must_include": ["tracking"]},
        },
        1,
    )

    results = run_evaluation(
        cases=[case],
        candidates=[create_adapter("mock:careful")],
        judge=None,
        output=tmp_path,
        source_path="test.jsonl",
        max_workers=1,
    )

    assert results[0].score == 100
    assert (tmp_path / "report.html").exists()
    record = json.loads((tmp_path / "results.jsonl").read_text().strip())
    assert record["model"] == "mock:careful"
    assert json.loads((tmp_path / "manifest.json").read_text())["case_count"] == 1

