"""Small process boundary for the live browser/provider attempt."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .environment import state_view


LIVE_SCRIPT = Path(__file__).resolve().parents[3] / "trajectory-runtime" / "live-attempt.mjs"
_SECRET_PATTERN = re.compile(r"(?:sk|rk)-[A-Za-z0-9_-]{6,}")


def _redact(value: str) -> str:
    return _SECRET_PATTERN.sub("[REDACTED]", value)


def run_live_attempt(
    public_task: dict[str, Any],
    arm: str,
    initial_state: dict[str, Any],
) -> dict[str, Any]:
    """Run one live attempt without exposing the provider credential to Python logs."""

    node = shutil.which("node") or "node"
    payload = {
        "task": public_task,
        "arm": arm,
        # The browser fixture returns its public state view, not its private cart shape.
        "initial_state": state_view(initial_state),
    }
    try:
        completed = subprocess.run(
            [node, str(LIVE_SCRIPT)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=115,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("live attempt subprocess exceeded its provider deadline") from exc
    if completed.returncode != 0:
        detail = _redact(completed.stderr.strip())[-4000:]
        raise RuntimeError(f"live attempt failed: {detail or 'node exited without diagnostics'}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = _redact(completed.stdout.strip())[-1000:]
        raise RuntimeError(f"live attempt returned invalid JSON: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("live attempt result must be an object")
    return result


__all__ = ["LIVE_SCRIPT", "run_live_attempt"]
