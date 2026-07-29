import json

import pytest

from eval_harness.cases import CaseValidationError, load_cases


def test_loads_valid_case(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "one",
                "input": "Say hello.",
                "rubric": [{"id": "correct", "description": "Says hello.", "weight": 2}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_cases(path)

    assert cases[0].id == "one"
    assert cases[0].rubric[0].weight == 2


def test_rejects_duplicate_case_ids(tmp_path):
    path = tmp_path / "cases.jsonl"
    case = {
        "id": "same",
        "input": "Say hello.",
        "rubric": [{"id": "correct", "description": "Says hello."}],
    }
    path.write_text(json.dumps(case) + "\n" + json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(CaseValidationError, match="duplicate case id"):
        load_cases(path)

