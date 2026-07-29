from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Criterion:
    id: str
    description: str
    weight: float = 1.0


@dataclass(frozen=True)
class Checks:
    must_include: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()
    max_chars: int | None = None


@dataclass(frozen=True)
class EvalCase:
    id: str
    input: str
    rubric: tuple[Criterion, ...]
    system: str = ""
    reference: str = ""
    checks: Checks = Checks()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Generation:
    text: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_id: str | None = None


@dataclass
class CriterionScore:
    criterion_id: str
    score: float
    reason: str


@dataclass
class Judgment:
    scores: list[CriterionScore]
    overall_reason: str
    judge: str


@dataclass
class CheckResult:
    passed: bool
    details: list[str]


@dataclass
class EvalResult:
    case_id: str
    model: str
    response: str
    score: float
    rubric_score: float
    checks: CheckResult
    judgment: Judgment
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    metadata: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

