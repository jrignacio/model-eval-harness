from __future__ import annotations

import json
import re
from dataclasses import asdict

from .models import ModelAdapter
from .types import CheckResult, CriterionScore, EvalCase, Judgment

JUDGE_SYSTEM = """You are a strict evaluator. Judge only against the supplied case and rubric.
Do not infer requirements that are not stated. Return one JSON object and no markdown:
{"scores":[{"criterion_id":"...","score":0,"reason":"..."}],"overall_reason":"..."}
Use every criterion exactly once. Score each from 0 to 4:
0 = absent or contradicts the requirement
1 = major problems
2 = partly meets it
3 = meets it with minor issues
4 = fully meets it
Keep reasons concrete and brief."""


def run_checks(case: EvalCase, response: str) -> CheckResult:
    folded = response.casefold()
    details: list[str] = []
    for phrase in case.checks.must_include:
        if phrase.casefold() not in folded:
            details.append(f"missing required phrase: {phrase}")
    for phrase in case.checks.must_not_include:
        if phrase.casefold() in folded:
            details.append(f"contains prohibited phrase: {phrase}")
    if case.checks.max_chars is not None and len(response) > case.checks.max_chars:
        details.append(f"too long: {len(response)} characters (max {case.checks.max_chars})")
    return CheckResult(passed=not details, details=details)


def heuristic_judgment(case: EvalCase, response: str) -> Judgment:
    checks = run_checks(case, response)
    base = 4.0 if checks.passed else 2.0
    scores = [
        CriterionScore(
            criterion_id=criterion.id,
            score=base,
            reason=(
                "Passed the deterministic proxy checks."
                if checks.passed
                else "One or more deterministic proxy checks failed."
            ),
        )
        for criterion in case.rubric
    ]
    return Judgment(
        scores=scores,
        overall_reason="Heuristic plumbing check; use a model or human judge for semantic quality.",
        judge="heuristic",
    )


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise ValueError("judge did not return a JSON object")
        value = json.loads(match.group())
    if not isinstance(value, dict):
        raise TypeError("judge output must be a JSON object")
    return value


def model_judgment(case: EvalCase, response: str, judge: ModelAdapter) -> Judgment:
    prompt = json.dumps(
        {
            "case": {
                "input": case.input,
                "reference": case.reference,
                "rubric": [asdict(item) for item in case.rubric],
            },
            "candidate_response": response,
        },
        ensure_ascii=False,
    )
    generation = judge.generate(system=JUDGE_SYSTEM, prompt=prompt, max_tokens=1200)
    raw = _extract_json(generation.text)
    raw_scores = raw.get("scores")
    if not isinstance(raw_scores, list):
        raise TypeError("judge output is missing scores")

    expected = {criterion.id for criterion in case.rubric}
    scores: list[CriterionScore] = []
    for item in raw_scores:
        if not isinstance(item, dict):
            raise TypeError("each judge score must be an object")
        criterion_id = item.get("criterion_id")
        score = item.get("score")
        reason = item.get("reason")
        if criterion_id not in expected:
            raise ValueError(f"judge returned unknown criterion {criterion_id!r}")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 4:
            raise ValueError(f"invalid score for criterion {criterion_id!r}")
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"missing reason for criterion {criterion_id!r}")
        scores.append(CriterionScore(criterion_id, float(score), reason))
    if {score.criterion_id for score in scores} != expected or len(scores) != len(expected):
        raise ValueError("judge must score every criterion exactly once")
    overall_reason = raw.get("overall_reason", "")
    if not isinstance(overall_reason, str):
        raise TypeError("overall_reason must be a string")
    return Judgment(scores=scores, overall_reason=overall_reason, judge=judge.name)


def jev_judgment(case: EvalCase, response: str) -> Judgment:
    """Score-primitive judge via TypeSafe/Jev. No generative rationale --
    each criterion gets a typed 0-4 Score instead of a parsed JSON blob."""
    from typesafe_sdk import TypeSafeClient, Score

    questions = {
        criterion.id: Score(
            instructions=(
                f"Case input: {case.input}\n\n"
                f"Reference answer: {case.reference}\n\n"
                f"Candidate response: {response}\n\n"
                f"Criterion: {criterion.description}\n"
                "How well does the candidate response meet this criterion?"
            ),
            criteria=["absent or contradicts", "major problems", "partly meets", "meets with minor issues", "fully meets"],
        )
        for criterion in case.rubric
    }

    with TypeSafeClient() as client:
        result = client.system_one(state={"case_id": case.id}, questions=questions)

    expected = {criterion.id for criterion in case.rubric}
    scores = [
        CriterionScore(
            criterion_id=criterion_id,
            score=float(answer.score),
            reason=f"Score primitive (confidence {answer.confidence:.2f}); no generative rationale.",
        )
        for criterion_id, answer in result.answers.items()
    ]
    if {score.criterion_id for score in scores} != expected:
        raise ValueError("jev judge did not return every rubric criterion")

    return Judgment(
        scores=scores,
        overall_reason="",
        judge="jev:jev-latest",
    )


def weighted_score(case: EvalCase, judgment: Judgment) -> float:
    by_id = {score.criterion_id: score.score for score in judgment.scores}
    weighted = sum(by_id[item.id] * item.weight for item in case.rubric)
    maximum = sum(item.weight for item in case.rubric) * 4
    return round(weighted / maximum * 100, 1)
