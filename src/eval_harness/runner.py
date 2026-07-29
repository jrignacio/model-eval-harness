from __future__ import annotations

import json
import platform
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .judge import heuristic_judgment, model_judgment, run_checks, weighted_score
from .models import ModelAdapter
from .report import write_report
from .types import CheckResult, EvalCase, EvalResult, Judgment


def _evaluate(
    case: EvalCase,
    candidate: ModelAdapter,
    judge: ModelAdapter | None,
    max_tokens: int,
    temperature: float,
) -> EvalResult:
    generation = candidate.generate(
        system=case.system,
        prompt=case.input,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    checks = run_checks(case, generation.text)
    judgment = (
        model_judgment(case, generation.text, judge)
        if judge is not None
        else heuristic_judgment(case, generation.text)
    )
    rubric_score = weighted_score(case, judgment)
    score = min(rubric_score, 60.0) if not checks.passed else rubric_score
    return EvalResult(
        case_id=case.id,
        model=candidate.name,
        response=generation.text,
        score=score,
        rubric_score=rubric_score,
        checks=checks,
        judgment=judgment,
        latency_ms=generation.latency_ms,
        input_tokens=generation.input_tokens,
        output_tokens=generation.output_tokens,
        metadata=case.metadata,
    )


def _error_result(case: EvalCase, model: str, error: Exception) -> EvalResult:
    return EvalResult(
        case_id=case.id,
        model=model,
        response="",
        score=0,
        rubric_score=0,
        checks=CheckResult(False, ["generation or judging failed"]),
        judgment=Judgment([], "Evaluation failed.", "none"),
        latency_ms=0,
        input_tokens=None,
        output_tokens=None,
        metadata=case.metadata,
        error=f"{type(error).__name__}: {error}",
    )


def run_evaluation(
    *,
    cases: list[EvalCase],
    candidates: list[ModelAdapter],
    judge: ModelAdapter | None,
    output: Path,
    source_path: str,
    max_workers: int = 4,
    max_tokens: int = 1200,
    temperature: float = 0,
) -> list[EvalResult]:
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    work = [(case, candidate) for case in cases for candidate in candidates]
    results: list[EvalResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _evaluate, case, candidate, judge, max_tokens, temperature
            ): (case, candidate)
            for case, candidate in work
        }
        for future in as_completed(futures):
            case, candidate = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - one failed cell must not abort the matrix
                result = _error_result(case, candidate.name, exc)
            results.append(result)
            status = "error" if result.error else f"{result.score:.1f}"
            print(f"{result.case_id:<24} {result.model:<36} {status}")

    results.sort(key=lambda item: (item.case_id, item.model))
    with (output / "results.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    finished = datetime.now(UTC)
    manifest = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "case_source": source_path,
        "case_count": len(cases),
        "models": [candidate.name for candidate in candidates],
        "judge": judge.name if judge else "heuristic",
        "settings": {
            "max_workers": max_workers,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "cases": [
            {
                "id": case.id,
                "input": case.input,
                "reference": case.reference,
                "rubric": [asdict(item) for item in case.rubric],
                "metadata": case.metadata,
            }
            for case in cases
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(output / "report.html", manifest, results)
    return results
