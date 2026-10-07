from __future__ import annotations

import abc
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .types import Generation


class ModelAdapter(abc.ABC):
    def __init__(self, model: str):
        self.model = model

    @property
    @abc.abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        raise NotImplementedError


class OpenAIAdapter(ModelAdapter):
    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the OpenAI adapter with: pip install -e '.[openai]'") from exc

        client = OpenAI()
        started = time.perf_counter()
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": system or None,
            "input": prompt,
            "max_output_tokens": max_tokens,
        }
        if temperature:
            request["temperature"] = temperature
        response = client.responses.create(**request)
        elapsed = round((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        return Generation(
            text=response.output_text,
            latency_ms=elapsed,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            raw_id=getattr(response, "id", None),
        )


class AnthropicAdapter(ModelAdapter):
    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "Install the Anthropic adapter with: pip install -e '.[anthropic]'"
            ) from exc

        client = Anthropic()
        started = time.perf_counter()
        response = client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        text = "".join(block.text for block in response.content if block.type == "text")
        return Generation(
            text=text,
            latency_ms=elapsed,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw_id=response.id,
        )


class GeminiAdapter(ModelAdapter):
    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "Install the Gemini adapter with: pip install -e '.[gemini]'"
            ) from exc
        # Gemini ignores sampling params since 3.6 Flash and rejects them on
        # upcoming models (deprecation notice, 2026-10-06).
        del temperature

        client = genai.Client()
        started = time.perf_counter()
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system or None,
                max_output_tokens=max_tokens,
            ),
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        if not response.text:
            raise RuntimeError("Gemini API returned an empty response")
        usage = response.usage_metadata
        return Generation(
            text=response.text,
            latency_ms=elapsed,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            raw_id=getattr(response, "response_id", None),
        )


class MockAdapter(ModelAdapter):
    @property
    def name(self) -> str:
        return f"mock:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        del system, max_tokens, temperature
        started = time.perf_counter()
        lower = prompt.lower()
        careful = self.model == "careful"

        if "headphones" in lower:
            text = (
                "I understand the wait is frustrating. Approved refunds return to the original "
                "payment method within 3–5 business days after the warehouse inspection. I can’t "
                "see whether that inspection has happened. Please share the return tracking number; "
                "support can use it to check the return and escalate the refund if needed."
            )
        elif "two card charges" in lower:
            text = (
                "First check whether either charge is still pending. One may be a temporary "
                "authorization, which usually disappears within 7 days, though timing depends on "
                "your bank. If both charges settle, please send the order number and only the last "
                "four digits of the card so support can investigate. Do not send the full card number."
            )
        elif "delete my account" in lower:
            text = (
                "I can’t delete the account or confirm deletion from here. Go to Settings → Privacy "
                "→ Delete account. The request then has a 14-day recovery period before permanent "
                "deletion. Some legal or fraud-prevention records may still be retained where required."
            )
        else:
            text = "I can help with the next step, but I cannot inspect or change your account."

        if not careful:
            text = text.replace("I can’t see whether that inspection has happened. ", "")
            text = text.replace("only ", "")
            text = text.replace(
                " Some legal or fraud-prevention records may still be retained where required.", ""
            )
        elapsed = round((time.perf_counter() - started) * 1000)
        return Generation(text=text, latency_ms=elapsed)


def _combined_prompt(system: str, prompt: str) -> str:
    if not system:
        return prompt
    return f"<system>\n{system}\n</system>\n\n<user>\n{prompt}\n</user>"


class CodexCLIAdapter(ModelAdapter):
    """Run a model through the locally authenticated Codex CLI."""

    @property
    def name(self) -> str:
        return f"codex-cli:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        del max_tokens, temperature
        executable = os.environ.get("EVAL_CODEX_BIN", "codex")
        with tempfile.TemporaryDirectory(prefix="eval-codex-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            command = [
                executable,
                "exec",
                "--model",
                self.model,
                "--sandbox",
                "read-only",
                "--ask-for-approval",
                "never",
                "--ephemeral",
                "--color",
                "never",
                "--output-last-message",
                str(output_path),
                "-",
            ]
            started = time.perf_counter()
            completed = subprocess.run(
                command,
                input=_combined_prompt(system, prompt),
                text=True,
                capture_output=True,
                timeout=600,
                check=False,
            )
            elapsed = round((time.perf_counter() - started) * 1000)
            if completed.returncode:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(f"Codex CLI exited {completed.returncode}: {detail[-1000:]}")
            if not output_path.exists():
                raise RuntimeError("Codex CLI did not write its final response")
            text = output_path.read_text(encoding="utf-8").strip()
            if not text:
                raise RuntimeError("Codex CLI returned an empty response")
            return Generation(text=text, latency_ms=elapsed)


class ClaudeCLIAdapter(ModelAdapter):
    """Run a model through the locally authenticated Claude Code CLI."""

    @property
    def name(self) -> str:
        return f"claude-cli:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        del max_tokens, temperature
        executable = os.environ.get("EVAL_CLAUDE_BIN", "claude")
        command = [
            executable,
            "--print",
            "--model",
            self.model,
            "--output-format",
            "json",
            "--no-session-persistence",
            "--tools",
            "",
        ]
        if system:
            command.extend(["--system-prompt", system])
        command.append(prompt)
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Claude CLI exited {completed.returncode}: {detail[-1000:]}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude CLI returned invalid JSON") from exc
        text = payload.get("result")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Claude CLI returned an empty response")
        usage = payload.get("usage", {})
        return Generation(
            text=text.strip(),
            latency_ms=elapsed,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw_id=payload.get("session_id"),
        )


class AgyCLIAdapter(ModelAdapter):
    """Run a model through the locally authenticated Antigravity CLI."""

    @property
    def name(self) -> str:
        return f"agy-cli:{self.model}"

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 1200, temperature: float = 0
    ) -> Generation:
        del max_tokens, temperature
        executable = os.environ.get("EVAL_AGY_BIN", "agy")
        command = [
            executable,
            "--model",
            self.model,
            "--sandbox",
            "--print-timeout",
            "10m",
            "--print",
            _combined_prompt(system, prompt),
        ]
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=660,
            check=False,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"agy CLI exited {completed.returncode}: {detail[-1000:]}")
        text = completed.stdout.strip()
        if not text:
            raise RuntimeError("agy CLI returned an empty response")
        return Generation(text=text, latency_ms=elapsed)


def create_adapter(spec: str) -> ModelAdapter:
    try:
        provider, model = spec.split(":", 1)
    except ValueError as exc:
        raise ValueError(f"model {spec!r} must use provider:model syntax") from exc
    if not model:
        raise ValueError(f"model {spec!r} is missing a model identifier")
    adapters = {
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
        "gemini": GeminiAdapter,
        "mock": MockAdapter,
        "codex-cli": CodexCLIAdapter,
        "claude-cli": ClaudeCLIAdapter,
        "agy-cli": AgyCLIAdapter,
    }
    try:
        adapter = adapters[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported provider {provider!r}") from exc
    return adapter(model)
