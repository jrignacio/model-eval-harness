from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

from .types import EvalResult


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _aggregate(results: list[EvalResult]) -> list[dict]:
    grouped: dict[str, list[EvalResult]] = defaultdict(list)
    for result in results:
        grouped[result.model].append(result)
    rows = []
    for model, items in sorted(grouped.items()):
        successful = [item for item in items if not item.error]
        rows.append(
            {
                "model": model,
                "score": (
                    round(sum(item.score for item in successful) / len(successful), 1)
                    if successful
                    else 0
                ),
                "pass_rate": round(
                    sum(item.checks.passed for item in items) / len(items) * 100, 1
                ),
                "latency": (
                    round(sum(item.latency_ms for item in successful) / len(successful))
                    if successful
                    else 0
                ),
                "errors": sum(bool(item.error) for item in items),
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def write_report(path: Path, manifest: dict, results: list[EvalResult]) -> None:
    summary = _aggregate(results)
    cases = {item["id"]: item for item in manifest["cases"]}
    summary_rows = "".join(
        f"""<tr>
          <td><strong>{_e(row['model'])}</strong></td>
          <td><span class="score">{row['score']:.1f}</span></td>
          <td>{row['pass_rate']:.1f}%</td>
          <td>{row['latency']} ms</td>
          <td>{row['errors']}</td>
        </tr>"""
        for row in summary
    )

    cards = []
    for result in results:
        case = cases[result.case_id]
        criteria = {
            item["id"]: item for item in case["rubric"]
        }
        judgment_rows = "".join(
            f"""<tr>
              <td>{_e(score.criterion_id)}</td>
              <td>{score.score:.1f}/4</td>
              <td>{_e(criteria[score.criterion_id]['weight'])}</td>
              <td>{_e(score.reason)}</td>
            </tr>"""
            for score in result.judgment.scores
        )
        check_text = (
            "All deterministic checks passed."
            if result.checks.passed
            else "; ".join(result.checks.details)
        )
        error = (
            f'<div class="error"><strong>Error:</strong> {_e(result.error)}</div>'
            if result.error
            else ""
        )
        cards.append(
            f"""<article class="case">
              <div class="case-head">
                <div>
                  <div class="eyebrow">{_e(result.case_id)}</div>
                  <h3>{_e(result.model)}</h3>
                </div>
                <div class="score score-large">{result.score:.1f}</div>
              </div>
              {error}
              <details>
                <summary>Prompt and reference</summary>
                <h4>Prompt</h4><pre>{_e(case['input'])}</pre>
                <h4>Reference</h4><pre>{_e(case['reference'])}</pre>
              </details>
              <h4>Response</h4>
              <pre class="response">{_e(result.response)}</pre>
              <div class="check {'pass' if result.checks.passed else 'fail'}">
                {_e(check_text)}
              </div>
              <table class="rubric">
                <thead><tr><th>Criterion</th><th>Score</th><th>Weight</th><th>Reason</th></tr></thead>
                <tbody>{judgment_rows}</tbody>
              </table>
              <p class="reason">{_e(result.judgment.overall_reason)}</p>
              <div class="meta">Rubric {result.rubric_score:.1f} · {result.latency_ms} ms ·
                input tokens {_e(result.input_tokens if result.input_tokens is not None else 'n/a')} ·
                output tokens {_e(result.output_tokens if result.output_tokens is not None else 'n/a')}
              </div>
            </article>"""
        )

    data = json.dumps(
        [{"case": r.case_id, "model": r.model, "score": r.score} for r in results],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Model evaluation report</title>
  <style>
    :root {{ --ink:#19211d; --muted:#65716a; --paper:#f5f1e8; --card:#fffdf8;
      --line:#d8d2c5; --accent:#ef5b35; --green:#237a57; --red:#a33b2b; }}
    * {{ box-sizing:border-box }}
    body {{ margin:0; color:var(--ink); background:var(--paper);
      font:15px/1.55 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
    main {{ width:min(1120px,calc(100% - 32px)); margin:0 auto; padding:64px 0 }}
    header {{ display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; margin-bottom:40px }}
    h1 {{ margin:0; font:700 clamp(38px,7vw,72px)/.96 Georgia,serif; letter-spacing:-.04em }}
    h2 {{ margin:48px 0 18px; font-size:24px }} h3 {{ margin:3px 0 0; font-size:19px }}
    h4 {{ margin:20px 0 7px; font-size:12px; text-transform:uppercase; letter-spacing:.08em }}
    p {{ margin:8px 0 }} .eyebrow,.meta {{ color:var(--muted); font-size:12px }}
    .run-meta {{ text-align:right; color:var(--muted) }}
    table {{ width:100%; border-collapse:collapse }} th,td {{ padding:12px; text-align:left;
      border-bottom:1px solid var(--line); vertical-align:top }} th {{ color:var(--muted);
      font-size:11px; text-transform:uppercase; letter-spacing:.08em }}
    .leaderboard {{ background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden }}
    .score {{ display:inline-grid; place-items:center; min-width:56px; padding:5px 9px;
      background:var(--ink); color:white; border-radius:999px; font-weight:750 }}
    .score-large {{ width:72px; height:72px; border-radius:50%; font-size:21px }}
    .case {{ margin:18px 0; padding:24px; background:var(--card); border:1px solid var(--line);
      border-radius:14px; box-shadow:0 8px 30px rgba(25,33,29,.04) }}
    .case-head {{ display:flex; align-items:center; justify-content:space-between; gap:24px }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; padding:14px; margin:6px 0;
      border-radius:8px; background:#f0ece2; font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace }}
    .response {{ background:#202823; color:#f9f6ed }}
    summary {{ cursor:pointer; color:var(--muted); margin-top:12px }}
    .check,.error {{ padding:10px 12px; margin:12px 0; border-radius:8px; font-size:13px }}
    .pass {{ color:var(--green); background:#e8f5ee }} .fail,.error {{ color:var(--red); background:#faeae6 }}
    .rubric {{ margin-top:12px; font-size:13px }} .reason {{ font-style:italic; color:var(--muted) }}
    .meta {{ margin-top:16px; padding-top:12px; border-top:1px solid var(--line) }}
    footer {{ margin-top:48px; color:var(--muted); font-size:12px }}
    @media(max-width:700px) {{ header {{ grid-template-columns:1fr }} .run-meta {{ text-align:left }}
      .rubric th:nth-child(3),.rubric td:nth-child(3) {{ display:none }} main {{ padding-top:32px }} }}
  </style>
</head>
<body><main>
  <header>
    <div><div class="eyebrow">EVALUATION HARNESS</div><h1>Model report</h1></div>
    <div class="run-meta">{_e(manifest['case_count'])} cases · {_e(len(manifest['models']))} models<br>
      Judge: {_e(manifest['judge'])}<br>{_e(manifest['finished_at'])}</div>
  </header>
  <h2>Leaderboard</h2>
  <div class="leaderboard"><table>
    <thead><tr><th>Model</th><th>Mean score</th><th>Check pass rate</th><th>Mean latency</th><th>Errors</th></tr></thead>
    <tbody>{summary_rows}</tbody>
  </table></div>
  <h2>Case evidence</h2>
  {''.join(cards)}
  <footer>Generated from auditable JSONL results. Scores are evidence, not ground truth.</footer>
  <script type="application/json" id="eval-data">{data}</script>
</main></body></html>"""
    path.write_text(document, encoding="utf-8")

