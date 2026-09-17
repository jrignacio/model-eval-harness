"""Pilot: does the Jev Score judge (judge.jev_judgment) actually discriminate
between a careful and a less-careful response on the criteria that matter?

Uses the harness's own MockAdapter so this costs zero LLM spend to generate
candidate responses -- only the judge calls hit the real Jev API. Not part
of the eval-harness CLI; run directly:

    op run --env-file=.env -- ./.venv/bin/python tests/jev_judge_pilot.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eval_harness.cases import load_cases
from eval_harness.judge import jev_judgment, weighted_score
from eval_harness.models import MockAdapter

CASES_PATH = Path(__file__).resolve().parent.parent / "cases" / "support.jsonl"


def main():
    cases = load_cases(str(CASES_PATH))
    careful = MockAdapter("careful")
    sloppy = MockAdapter("sloppy")

    for case in cases:
        careful_resp = careful.generate(system=case.system, prompt=case.input).text
        sloppy_resp = sloppy.generate(system=case.system, prompt=case.input).text

        careful_judgment = jev_judgment(case, careful_resp)
        sloppy_judgment = jev_judgment(case, sloppy_resp)

        careful_pct = weighted_score(case, careful_judgment)
        sloppy_pct = weighted_score(case, sloppy_judgment)

        print(f"\n=== {case.id} ===")
        print(f"careful weighted score: {careful_pct}%")
        print(f"sloppy  weighted score: {sloppy_pct}%")
        by_id_careful = {s.criterion_id: s.score for s in careful_judgment.scores}
        by_id_sloppy = {s.criterion_id: s.score for s in sloppy_judgment.scores}
        for criterion in case.rubric:
            c, s = by_id_careful[criterion.id], by_id_sloppy[criterion.id]
            flag = "" if c >= s else "  <-- SLOPPY SCORED HIGHER (bad)"
            print(f"  {criterion.id:<20} careful={c:.2f}  sloppy={s:.2f}{flag}")

        verdict = "PASS" if careful_pct > sloppy_pct else "FAIL"
        print(f"  verdict: {verdict} (careful should outscore sloppy)")


if __name__ == "__main__":
    main()
