import base64
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image

import utils.http_client as http_client
from utils.ai_utils import ClaudeTextGenerator, OpenAIImageGenerator, OpenAITextGenerator
from utils.http_client import post_with_retries


def response(status, body=None, headers=None):
    result = Mock(status_code=status, ok=status < 400, headers=headers or {}, text=str(body))
    result.json.return_value = body
    return result


@pytest.fixture
def session(monkeypatch):
    """The shared HTTP session, answering POSTs from `session.replies` in order."""
    session = Mock()
    session.replies = []
    session.post.side_effect = lambda url, **kwargs: session.replies.pop(0)
    monkeypatch.setattr(http_client, "get_http_session", lambda: session)
    monkeypatch.setattr(http_client.time, "sleep", lambda seconds: session.waits.append(seconds))
    session.waits = []
    return session


class TestRetries:

    def test_retries_temporary_failures_with_backoff(self, session):
        session.replies = [response(529), response(503), response(200, {})]
        assert post_with_retries("https://api").status_code == 200
        assert session.waits == [1, 2]

    def test_waits_as_long_as_retry_after_asks_up_to_a_limit(self, session):
        session.replies = [response(429, headers={"retry-after": "7"}), response(429, headers={"retry-after": "900"}),
                           response(200)]
        post_with_retries("https://api")
        assert session.waits == [7, http_client.MAX_RETRY_WAIT_SECONDS]

    def test_gives_up_after_the_last_retry(self, session):
        session.replies = [response(500), response(500), response(500)]
        assert post_with_retries("https://api", retries=2).status_code == 500
        assert session.post.call_count == 3

    def test_does_not_retry_other_errors_or_what_is_ruled_out(self, session):
        session.replies = [response(400)]
        assert post_with_retries("https://api").status_code == 400
        session.replies = [response(429)]
        assert post_with_retries("https://api", is_retryable=lambda r: False).status_code == 429
        assert session.waits == []


class TestClaude:

    def test_sends_a_message_and_returns_the_text_after_thinking(self, session):
        session.replies = [response(200, {"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": "..."}, {"type": "text", "text": " Hi \n"}]})]
        assert ClaudeTextGenerator("key").generate_text("claude-haiku-4-5", "system", "user", 0.7) == "Hi"
        url, kwargs = session.post.call_args.args[0], session.post.call_args.kwargs
        assert url == "https://api.anthropic.com/v1/messages"
        assert kwargs["headers"] == {"x-api-key": "key", "anthropic-version": "2023-06-01"}
        assert kwargs["json"] == {"model": "claude-haiku-4-5", "max_tokens": 16000, "system": "system",
                                  "temperature": 0.7, "messages": [{"role": "user", "content": "user"}]}

    def test_claude_5_skips_temperature_and_opts_into_fallback(self, session):
        session.replies = [response(200, {"content": [{"type": "text", "text": "yes"}]})]
        ClaudeTextGenerator("key").generate_text("claude-opus-5-5", "system", "user", 0.7)
        kwargs = session.post.call_args.kwargs
        assert "temperature" not in kwargs["json"]
        assert kwargs["json"]["fallbacks"] == "default"
        assert kwargs["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"

    def test_reports_the_api_error_and_refusals(self, session):
        session.replies = [response(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                                     "message": "bad model"}})]
        with pytest.raises(RuntimeError, match="400: bad model"):
            ClaudeTextGenerator("key").generate_text("claude-x", "system", "user")
        session.replies = [response(200, {"stop_reason": "refusal", "stop_details": "nope", "content": []})]
        with pytest.raises(RuntimeError, match="declined"):
            ClaudeTextGenerator("key").generate_text("claude-x", "system", "user")

    def test_asks_about_an_image_by_url(self, session):
        session.replies = [response(200, {"content": [{"type": "text", "text": "Yes."}]})]
        assert ClaudeTextGenerator("key").answer_yes_no("claude-haiku-4-5", "A deer?", "https://img")
        content = session.post.call_args.kwargs["json"]["messages"][0]["content"]
        assert content[0] == {"type": "image", "source": {"type": "url", "url": "https://img"}}


class TestOpenAI:

    def test_chat_converts_temperature_and_returns_the_text(self, session):
        session.replies = [response(200, {"choices": [{"message": {"content": " Hello "}}]})]
        assert OpenAITextGenerator("key").generate_text("gpt-4o", "system", "user", 0.5) == "Hello"
        kwargs = session.post.call_args.kwargs
        assert session.post.call_args.args[0] == "https://api.openai.com/v1/chat/completions"
        assert kwargs["headers"] == {"Authorization": "Bearer key"}
        assert kwargs["json"]["temperature"] == 1.0

    def test_an_exhausted_quota_is_not_retried(self, session):
        quota = {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted", "message": "No credits"}}
        session.replies = [response(429, quota)]
        with pytest.raises(RuntimeError, match="No credits"):
            OpenAITextGenerator("key").generate_text("gpt-4o", "system", "user")
        assert session.waits == []

    def test_image_generation_decodes_the_image(self, session):
        png = BytesIO()
        Image.new("RGB", (4, 2)).save(png, "PNG")
        session.replies = [response(200, {"data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}]})]
        image = OpenAIImageGenerator("key").generate_image("gpt-image-1", "a fox", use_cache=False)
        assert image.size == (4, 2)
        assert session.post.call_args.args[0] == "https://api.openai.com/v1/images/generations"


def test_the_sdks_are_not_imported():
    import sys
    assert "openai" not in sys.modules and "anthropic" not in sys.modules
