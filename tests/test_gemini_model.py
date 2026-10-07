import sys
from types import ModuleType, SimpleNamespace

from eval_harness.models import create_adapter


def test_gemini_adapter_uses_generate_content(monkeypatch):
    captured = {}

    class Config:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class Models:
        def generate_content(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                text="gemini answer",
                response_id="response-1",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=11,
                    candidates_token_count=4,
                ),
            )

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = lambda: SimpleNamespace(models=Models())
    fake_genai.types = SimpleNamespace(GenerateContentConfig=Config)
    fake_google = ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    generation = create_adapter("gemini:test-model").generate(
        system="Be useful.", prompt="Hello.", max_tokens=200, temperature=0.2
    )

    assert generation.text == "gemini answer"
    assert generation.input_tokens == 11
    assert generation.output_tokens == 4
    assert captured["request"]["model"] == "test-model"
    assert captured["request"]["contents"] == "Hello."
    assert captured["config"] == {
        "system_instruction": "Be useful.",
        "max_output_tokens": 200,
    }
