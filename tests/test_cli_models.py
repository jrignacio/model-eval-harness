import json
from pathlib import Path

from eval_harness.models import create_adapter


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_codex_cli_adapter_reads_last_message(tmp_path, monkeypatch):
    executable = tmp_path / "codex-stub"
    _write_executable(
        executable,
        """
output=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then output="$2"; shift 2; else shift; fi
done
printf 'codex answer' > "$output"
""",
    )
    monkeypatch.setenv("EVAL_CODEX_BIN", str(executable))

    generation = create_adapter("codex-cli:test-model").generate(
        system="Be useful.", prompt="Hello."
    )

    assert generation.text == "codex answer"


def test_claude_cli_adapter_parses_json(tmp_path, monkeypatch):
    executable = tmp_path / "claude-stub"
    payload = json.dumps(
        {
            "result": "claude answer",
            "session_id": "session-1",
            "usage": {"input_tokens": 12, "output_tokens": 5},
        }
    )
    _write_executable(executable, f"printf '%s' '{payload}'\n")
    monkeypatch.setenv("EVAL_CLAUDE_BIN", str(executable))

    generation = create_adapter("claude-cli:sonnet").generate(
        system="Be useful.", prompt="Hello."
    )

    assert generation.text == "claude answer"
    assert generation.input_tokens == 12
    assert generation.output_tokens == 5


def test_agy_cli_adapter_reads_stdout(tmp_path, monkeypatch):
    executable = tmp_path / "agy-stub"
    _write_executable(executable, "printf '%s' 'agy answer'\n")
    monkeypatch.setenv("EVAL_AGY_BIN", str(executable))

    generation = create_adapter("agy-cli:test-model").generate(
        system="Be useful.", prompt="Hello."
    )

    assert generation.text == "agy answer"
