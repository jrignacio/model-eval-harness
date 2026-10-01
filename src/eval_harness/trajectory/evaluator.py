"""Deterministic supervisor-side evaluation for offline trajectory attempts."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any


EVALUATOR_VERSION = "trajectory-evaluator/3"


class EvaluatorError(ValueError):
    """The task's deterministic evaluation contract is invalid."""


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split(".") if path else []:
        if not isinstance(current, dict) or part not in current:
            raise EvaluatorError(f"state path does not exist: {path}")
        current = current[part]
    return current


def _normalise_answer(value: str) -> str:
    """Turn punctuation and casing into a stable, word-boundary-safe form."""

    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_phrase(answer: str, phrase: str) -> bool:
    normalised_answer = _normalise_answer(answer)
    normalised_phrase = _normalise_answer(phrase)
    if not normalised_phrase:
        return False
    return f" {normalised_phrase} " in f" {normalised_answer} "


def _phrase_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str) or not item.strip() or not _normalise_answer(item)
            for item in value
        )
    ):
        raise EvaluatorError(f"{label} requires non-empty string values")
    return value


@dataclass(frozen=True)
class Evaluation:
    verdict: str
    passed: bool
    checks: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_version": EVALUATOR_VERSION,
            "verdict": self.verdict,
            "passed": self.passed,
            "checks": [copy.deepcopy(check) for check in self.checks],
        }


def _check(
    check: dict[str, Any],
    initial: dict[str, Any],
    final: dict[str, Any],
    answer: str,
    trace: list[dict[str, Any]],
) -> bool:
    kind = check.get("kind")
    if kind == "answer_contains":
        phrase = check.get("value")
        if not isinstance(phrase, str) or not phrase:
            raise EvaluatorError("answer_contains requires a non-empty value")
        return phrase.casefold() in answer.casefold()
    if kind == "answer_not_contains":
        phrase = check.get("value")
        if not isinstance(phrase, str) or not phrase:
            raise EvaluatorError("answer_not_contains requires a non-empty value")
        return phrase.casefold() not in answer.casefold()
    if kind == "answer_not_contains_any":
        values = check.get("values")
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
        ):
            raise EvaluatorError("answer_not_contains_any requires non-empty string values")
        folded = answer.casefold()
        return all(value.casefold() not in folded for value in values)
    if kind == "answer_equals":
        expected = check.get("value")
        if not isinstance(expected, str):
            raise EvaluatorError("answer_equals requires a string value")
        return answer == expected
    if kind == "answer_claims":
        claims = check.get("claims")
        if not isinstance(claims, list) or not claims:
            raise EvaluatorError("answer_claims requires a non-empty claims list")
        rejected = check.get("reject_any", [])
        if rejected:
            rejected = _phrase_list(rejected, "answer_claims reject_any")
        elif not isinstance(rejected, list):
            raise EvaluatorError("answer_claims reject_any must be a list")

        for phrase in rejected:
            if _contains_phrase(answer, phrase):
                return False

        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                raise EvaluatorError(f"answer_claims claim {index} must be an object")
            claim_id = claim.get("id")
            if not isinstance(claim_id, str) or not claim_id:
                raise EvaluatorError(f"answer_claims claim {index} needs a non-empty id")
            phrases = _phrase_list(claim.get("any"), f"answer_claims claim {claim_id} any")
            if not any(_contains_phrase(answer, phrase) for phrase in phrases):
                return False
        return True
    if kind == "state_equals":
        return final == check.get("expected")
    if kind == "state_field_equals":
        path = check.get("path")
        if not isinstance(path, str) or not path:
            raise EvaluatorError("state_field_equals requires a path")
        return _path_value(final, path) == check.get("expected")
    if kind == "state_unchanged":
        path = check.get("path")
        if not isinstance(path, str) or not path:
            raise EvaluatorError("state_unchanged requires a path")
        return _path_value(initial, path) == _path_value(final, path)
    if kind == "trace_tool_error":
        phrase = check.get("message_contains")
        if not isinstance(phrase, str) or not phrase:
            raise EvaluatorError("trace_tool_error requires message_contains")
        folded_phrase = phrase.casefold()
        for item in trace:
            if item.get("type") != "tool_result" or item.get("status") != "error":
                continue
            result = item.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("error"), str):
                continue
            if folded_phrase in result["error"].casefold():
                return True
        return False
    if kind == "trace_tool_error_or_snapshot":
        phrase = check.get("message_contains")
        if not isinstance(phrase, str) or not phrase:
            raise EvaluatorError("trace_tool_error_or_snapshot requires message_contains")
        folded_phrase = phrase.casefold()
        for item in trace:
            if item.get("type") != "tool_result":
                continue
            result = item.get("result")
            if item.get("status") == "error":
                if isinstance(result, dict) and isinstance(result.get("error"), str):
                    if folded_phrase in result["error"].casefold():
                        return True
            if item.get("name") == "snapshot":
                try:
                    visible = json.dumps(result, ensure_ascii=False).casefold()
                except (TypeError, ValueError):
                    continue
                if folded_phrase in visible:
                    return True
        return False
    raise EvaluatorError(f"unsupported evaluator check kind: {kind!r}")


def evaluate_task(
    task: dict[str, Any],
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    answer: str,
    trace: list[dict[str, Any]] | None = None,
) -> Evaluation:
    """Evaluate only supervisor-owned task data and an agent-visible answer."""

    if not isinstance(answer, str):
        raise EvaluatorError("answer must be a string")
    if trace is None:
        trace = []
    if not isinstance(trace, list) or any(not isinstance(item, dict) for item in trace):
        raise EvaluatorError("trace must be a list of objects")
    specification = task.get("evaluation")
    if not isinstance(specification, dict):
        raise EvaluatorError("task evaluation must be an object")
    checks = specification.get("checks")
    if not isinstance(checks, list) or not checks:
        raise EvaluatorError("task evaluation checks must be a non-empty list")

    results: list[dict[str, Any]] = []
    for index, raw_check in enumerate(checks):
        if not isinstance(raw_check, dict):
            raise EvaluatorError(f"evaluation check {index} must be an object")
        check_id = raw_check.get("id")
        if not isinstance(check_id, str) or not check_id:
            raise EvaluatorError(f"evaluation check {index} needs a non-empty id")
        passed = _check(raw_check, initial_state, final_state, answer, trace)
        results.append(
            {
                "id": check_id,
                "kind": raw_check.get("kind"),
                "passed": passed,
                "detail": "passed" if passed else "failed",
            }
        )

    passed = all(item["passed"] for item in results)
    return Evaluation(
        verdict="pass" if passed else "fail",
        passed=passed,
        checks=tuple(results),
    )


__all__ = ["EVALUATOR_VERSION", "Evaluation", "EvaluatorError", "evaluate_task"]
