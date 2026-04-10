"""Tests for the llm.py provider abstraction."""
import json
from unittest.mock import MagicMock, patch

import config
import llm


def _mock_ollama_response(text: str) -> MagicMock:
    """Return a mock that behaves like a streaming requests.Response.

    Produces one NDJSON token line per character followed by a done line,
    matching the Ollama streaming format that llm._collect_stream expects.
    """
    lines = [json.dumps({"response": text, "done": False}).encode()]
    lines.append(json.dumps({"response": "", "done": True}).encode())
    m = MagicMock()
    m.iter_lines.return_value = iter(lines)
    m.raise_for_status.return_value = None
    return m


def _mock_claude_response(text: str) -> MagicMock:
    m = MagicMock()
    m.json.return_value = {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    m.raise_for_status.return_value = None
    return m


class TestOllamaLocal:
    def test_calls_ollama_url(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama")
        monkeypatch.setattr(config, "OLLAMA_URL", "http://localhost:11400/api/generate")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3:32b")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")

        with patch("llm.requests.post", return_value=_mock_ollama_response("hello")) as mock:
            result = llm.generate("test prompt")

        assert result == "hello"
        call_args = mock.call_args
        assert call_args[0][0] == "http://localhost:11400/api/generate"
        body = call_args[1]["json"]
        assert body["model"] == "qwen3:32b"
        assert body["prompt"] == "test prompt"
        assert body["stream"] is True

    def test_uses_escalation_model_override(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3:32b")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "llama3:70b")

        with patch("llm.requests.post", return_value=_mock_ollama_response("ok")) as mock:
            llm.generate("test")

        body = mock.call_args[1]["json"]
        assert body["model"] == "llama3:70b"

    def test_json_format_included(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")

        with patch("llm.requests.post", return_value=_mock_ollama_response("{}")) as mock:
            llm.generate("test", format="json")

        body = mock.call_args[1]["json"]
        assert body["format"] == "json"

    def test_no_format_when_none(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")

        with patch("llm.requests.post", return_value=_mock_ollama_response("text")) as mock:
            llm.generate("test", format=None)

        body = mock.call_args[1]["json"]
        assert "format" not in body


class TestOllamaCloud:
    def test_uses_escalation_url_and_bearer(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama_cloud")
        monkeypatch.setattr(config, "ESCALATION_API_URL", "https://cloud.ollama.com")
        monkeypatch.setattr(config, "ESCALATION_API_KEY", "key-123")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "llama3:70b")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3:32b")

        with patch("llm.requests.post", return_value=_mock_ollama_response("ok")) as mock:
            result = llm.generate("test")

        assert result == "ok"
        call_args = mock.call_args
        assert "cloud.ollama.com" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer key-123"
        assert call_args[1]["json"]["model"] == "llama3:70b"


class TestClaude:
    def test_calls_anthropic_api(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "claude")
        monkeypatch.setattr(config, "ESCALATION_API_KEY", "sk-ant-test")
        monkeypatch.setattr(config, "ESCALATION_API_URL", "")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "claude-sonnet-4-20250514")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3:32b")

        with patch("llm.requests.post", return_value=_mock_claude_response('{"result":"ok"}')) as mock:
            result = llm.generate("analyze this", format="json")

        assert result == '{"result":"ok"}'
        call_args = mock.call_args
        assert "api.anthropic.com" in call_args[0][0]
        assert call_args[1]["headers"]["x-api-key"] == "sk-ant-test"
        body = call_args[1]["json"]
        assert body["model"] == "claude-sonnet-4-20250514"
        assert body["messages"][0]["content"] == "analyze this"
        assert "JSON" in body["system"]

    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "claude")
        monkeypatch.setattr(config, "ESCALATION_API_KEY", "")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")

        import pytest
        with pytest.raises(ValueError, match="ESCALATION_API_KEY"):
            llm.generate("test")

    def test_default_model(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "claude")
        monkeypatch.setattr(config, "ESCALATION_API_KEY", "sk-test")
        monkeypatch.setattr(config, "ESCALATION_API_URL", "")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "")

        with patch("llm.requests.post", return_value=_mock_claude_response("ok")) as mock:
            llm.generate("test")

        body = mock.call_args[1]["json"]
        assert body["model"] == "claude-sonnet-4-20250514"


class TestCollectStream:
    """Tests for _collect_stream thinking-token fallback."""

    def _make_stream(self, lines: list[dict]) -> MagicMock:
        m = MagicMock()
        m.iter_lines.return_value = iter(
            [json.dumps(l).encode() for l in lines]
        )
        return m

    def test_normal_response_tokens(self):
        resp = self._make_stream([
            {"response": '{"ok":true}', "done": False},
            {"response": "", "done": True},
        ])
        assert llm._collect_stream(resp) == '{"ok":true}'

    def test_strips_think_tags_from_response(self):
        resp = self._make_stream([
            {"response": "<think>reasoning</think>{\"ok\":true}", "done": False},
            {"response": "", "done": True},
        ])
        assert llm._collect_stream(resp) == '{"ok":true}'

    def test_fallback_to_thinking_field_when_response_empty(self):
        """When model puts answer in thinking NDJSON field, use it."""
        resp = self._make_stream([
            {"response": "", "thinking": '{"ok":true}', "done": False},
            {"response": "", "done": True},
        ])
        assert llm._collect_stream(resp) == '{"ok":true}'

    def test_thinking_field_with_think_tags(self):
        """Thinking field may contain <think> tags wrapping reasoning + answer."""
        resp = self._make_stream([
            {"response": "", "thinking": "<think>let me think</think>{\"ok\":true}", "done": False},
            {"response": "", "done": True},
        ])
        assert llm._collect_stream(resp) == '{"ok":true}'

    def test_response_preferred_over_thinking(self):
        """When both fields have content, response wins."""
        resp = self._make_stream([
            {"response": '{"from":"response"}', "thinking": "other stuff", "done": False},
            {"response": "", "done": True},
        ])
        assert llm._collect_stream(resp) == '{"from":"response"}'


class TestStripUntaggedThink:
    """Tests for _strip_untagged_think — catches CoT not wrapped in tags."""

    def test_strips_single_cot_paragraph(self):
        text = (
            "Okay, the user wants a briefing. I need to be concise.\n\n"
            "RV 7 and RV 10 are missing shields."
        )
        assert llm._strip_untagged_think(text) == "RV 7 and RV 10 are missing shields."

    def test_strips_multiple_cot_paragraphs(self):
        text = (
            "Okay, the user wants me to write a status briefing.\n\n"
            "Looking at the intel provided, there are several items.\n\n"
            "RV 7 and RV 10 are missing shields."
        )
        assert llm._strip_untagged_think(text) == "RV 7 and RV 10 are missing shields."

    def test_preserves_clean_text(self):
        text = "RV 7 and RV 10 are missing slew ring shields."
        assert llm._strip_untagged_think(text) == text

    def test_preserves_when_no_answer_follows(self):
        text = "Okay, the user wants a briefing about shields."
        assert llm._strip_untagged_think(text) == text

    def test_various_cot_starters(self):
        for starter in ["Let me ", "First, ", "I need to ", "I should ", "Now, "]:
            text = f"{starter}analyze this carefully.\n\nThe answer is 42."
            assert llm._strip_untagged_think(text) == "The answer is 42.", f"Failed for starter: {starter}"

    def test_generate_strips_untagged_for_freetext(self, monkeypatch):
        """generate() applies untagged-think stripping for format=None."""
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")
        cot_response = (
            "Okay, the user wants a briefing.\n\n"
            "RV 7 needs shield inspection."
        )
        with patch("llm.requests.post", return_value=_mock_ollama_response(cot_response)):
            result = llm.generate("test", format=None)
        assert result == "RV 7 needs shield inspection."

    def test_generate_skips_strip_for_json(self, monkeypatch):
        """generate() does NOT strip for json format (would break JSON)."""
        monkeypatch.setattr(config, "ESCALATION_PROVIDER", "ollama")
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")
        with patch("llm.requests.post", return_value=_mock_ollama_response('{"ok":true}')):
            result = llm.generate("test", format="json")
        assert result == '{"ok":true}'


class TestEffectiveModel:
    def test_returns_escalation_model_when_set(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_MODEL", "custom-model")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "default-model")
        assert config.effective_model() == "custom-model"

    def test_falls_back_to_ollama_model(self, monkeypatch):
        monkeypatch.setattr(config, "ESCALATION_MODEL", "")
        monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3:32b")
        assert config.effective_model() == "qwen3:32b"
