from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import Checks, Criterion, EvalCase


class CaseValidationError(ValueError):
    pass


def _require(value: dict[str, Any], key: str, expected: type, line: int) -> Any:
    item = value.get(key)
    if not isinstance(item, expected) or (expected is str and not item.strip()):
        raise CaseValidationError(f"line {line}: {key!r} must be a non-empty {expected.__name__}")
    return item


def parse_case(data: dict[str, Any], line: int) -> EvalCase:
    case_id = _require(data, "id", str, line)
    prompt = _require(data, "input", str, line)
    raw_rubric = _require(data, "rubric", list, line)
    if not raw_rubric:
        raise CaseValidationError(f"line {line}: rubric must contain at least one criterion")

    criteria: list[Criterion] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rubric):
        if not isinstance(raw, dict):
            raise CaseValidationError(f"line {line}: rubric item {index} must be an object")
        criterion_id = _require(raw, "id", str, line)
        if criterion_id in seen:
            raise CaseValidationError(f"line {line}: duplicate criterion id {criterion_id!r}")
        seen.add(criterion_id)
        description = _require(raw, "description", str, line)
        weight = raw.get("weight", 1)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise CaseValidationError(f"line {line}: criterion weight must be positive")
        criteria.append(Criterion(criterion_id, description, float(weight)))

    raw_checks = data.get("checks", {})
    if not isinstance(raw_checks, dict):
        raise CaseValidationError(f"line {line}: checks must be an object")
    must_include = raw_checks.get("must_include", [])
    must_not_include = raw_checks.get("must_not_include", [])
    if not all(isinstance(item, str) and item for item in must_include + must_not_include):
        raise CaseValidationError(f"line {line}: check phrases must be non-empty strings")
    max_chars = raw_checks.get("max_chars")
    if max_chars is not None and (
        not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0
    ):
        raise CaseValidationError(f"line {line}: max_chars must be a positive integer")

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise CaseValidationError(f"line {line}: metadata must be an object")

    return EvalCase(
        id=case_id,
        input=prompt,
        system=str(data.get("system", "")),
        reference=str(data.get("reference", "")),
        rubric=tuple(criteria),
        checks=Checks(tuple(must_include), tuple(must_not_include), max_chars),
        metadata=metadata,
    )


def load_cases(path: str | Path) -> list[EvalCase]:
    source = Path(path)
    cases: list[EvalCase] = []
    seen: set[str] = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaseValidationError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(raw, dict):
                raise CaseValidationError(f"line {line_number}: case must be an object")
            case = parse_case(raw, line_number)
            if case.id in seen:
                raise CaseValidationError(f"line {line_number}: duplicate case id {case.id!r}")
            seen.add(case.id)
            cases.append(case)
    if not cases:
        raise CaseValidationError(f"{source} contains no cases")
    return cases

