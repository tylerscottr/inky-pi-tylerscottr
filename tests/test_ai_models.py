from unittest.mock import Mock

import pytest

import utils.ai_models as ai_models
from utils.ai_models import IMAGE, TEXT, VISION, get_model_groups, model_picker
from utils.ai_utils import claude_accepts_temperature, claude_uses_fallback

OPENAI_IDS = ["gpt-5-mini", "gpt-4o", "gpt-4o-2024-08-06", "gpt-3.5-turbo", "o3", "o3-mini", "gpt-image-1",
              "dall-e-3", "whisper-1", "text-embedding-3-small", "gpt-4o-mini-tts", "gpt-realtime", "o1-pro"]

CLAUDE_MODELS = [
    {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "lifecycle": "active",
     "capabilities": {"image_input": {"supported": True}}},
    {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5", "capabilities": None},
    {"id": "claude-text-only", "display_name": "Text Only", "capabilities": {"image_input": {"supported": False}}},
    {"id": "claude-3-haiku-20240307", "display_name": "Old", "lifecycle": "deprecated"},
]


def response(body):
    result = Mock()
    result.json.return_value = body
    return result


@pytest.fixture
def providers(monkeypatch):
    """Fake provider APIs, keyed by which .env keys are set."""
    ai_models._cache.clear()
    keys = {"ANTHROPIC_API_KEY": "claude-key", "OPEN_AI_SECRET": "openai-key"}
    device_config = Mock()
    device_config.load_env_key.side_effect = keys.get

    def get(url, **kwargs):
        if "openai" in url:
            return response({"data": [{"id": i, "created": -n} for n, i in enumerate(OPENAI_IDS)]})
        return response({"data": CLAUDE_MODELS, "has_more": False})

    session = Mock()
    session.get.side_effect = get
    monkeypatch.setattr(ai_models, "get_http_session", lambda: session)
    return device_config, keys, session


def ids(groups):
    return {group["provider"]: [m["id"] for m in group["models"]] for group in groups}


def test_lists_models_by_capability(providers):
    device_config, _, _ = providers
    assert ids(get_model_groups(device_config, TEXT)) == {
        "Claude": ["claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-text-only"],
        "OpenAI": ["gpt-5-mini", "gpt-4o", "gpt-3.5-turbo", "o3", "o3-mini"],
    }
    assert ids(get_model_groups(device_config, VISION)) == {
        "Claude": ["claude-opus-5-5", "claude-haiku-4-5-20251001"],
        "OpenAI": ["gpt-5-mini", "gpt-4o", "o3"],
    }
    assert ids(get_model_groups(device_config, IMAGE)) == {"OpenAI": ["gpt-image-1"]}


def test_only_lists_providers_with_a_key(providers):
    device_config, keys, _ = providers
    del keys["OPEN_AI_SECRET"]
    assert list(ids(get_model_groups(device_config, TEXT))) == ["Claude"]


def test_falls_back_to_the_built_in_list_and_caches(providers):
    device_config, _, session = providers
    session.get.side_effect = RuntimeError("offline")
    groups = ids(get_model_groups(device_config, IMAGE))
    assert groups == {"OpenAI": ["gpt-image-1", "gpt-image-2"]}
    calls = session.get.call_count
    get_model_groups(device_config, TEXT)
    assert session.get.call_count == calls


class TestPicker:

    def test_matches_an_alias_to_its_dated_id(self, providers):
        device_config, _, _ = providers
        picker = model_picker(device_config, VISION, "claude-haiku-4-5")
        assert picker["selected"] == "claude-haiku-4-5-20251001"
        assert picker["unlisted"] is None

    def test_keeps_a_saved_model_that_is_not_listed(self, providers):
        device_config, _, _ = providers
        picker = model_picker(device_config, TEXT, "gpt-4-turbo")
        assert picker["selected"] == picker["unlisted"] == "gpt-4-turbo"

    def test_uses_the_default_then_the_first_model(self, providers):
        device_config, _, _ = providers
        assert model_picker(device_config, TEXT, None, default="o3")["selected"] == "o3"
        assert model_picker(device_config, TEXT, None, default="missing")["selected"] == "claude-opus-5-5"

    def test_has_nothing_without_keys(self, providers):
        device_config, keys, _ = providers
        keys.clear()
        assert model_picker(device_config, TEXT) == {"groups": [], "selected": None, "unlisted": None}


@pytest.mark.parametrize("model,temperature,fallback", [
    ("claude-haiku-4-5", True, False),
    ("claude-haiku-4-5-20251001", True, False),
    ("claude-3-7-sonnet-20250219", True, False),
    ("claude-opus-5-5", False, True),
    ("claude-fable-5-1", False, True),
])
def test_claude_model_rules(model, temperature, fallback):
    assert claude_accepts_temperature(model) == temperature
    assert claude_uses_fallback(model) == fallback
