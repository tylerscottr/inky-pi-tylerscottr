"""Discovers the AI models each configured provider offers, for the shared model picker."""
import hashlib
import logging
import re
import time

from utils.ai_utils import IMAGE_MODELS, OPENAI_TEXT_PREFIX, TEXT_MODELS, is_image_model
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

TEXT = "text"
VISION = "vision"
IMAGE = "image"
CAPABILITIES = (TEXT, VISION, IMAGE)

CACHE_SECONDS = 3600
FAILURE_CACHE_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 5

# Words that mark OpenAI models chat completions can't use, such as audio and embedding models.
OPENAI_EXCLUDED = ("audio", "realtime", "live", "tts", "transcribe", "search", "image", "embedding", "instruct",
                   "codex", "-pro", "deep-research", "computer-use", "moderation", "oss", "diarize")
OPENAI_VISION = re.compile(r"^(chatgpt-4o|gpt-4o|gpt-4\.1|o1$|o3$|o4)")
# Dated snapshots like gpt-4o-2024-08-06 or gpt-4-0613 duplicate their undated alias.
DATED_SNAPSHOT = re.compile(r"-\d{4}(-\d{2}-\d{2})?$")

_cache = {}


def _openai_supports(model_id, capability):
    if DATED_SNAPSHOT.search(model_id):
        return False
    if capability == IMAGE:
        return is_image_model(model_id)
    if not OPENAI_TEXT_PREFIX.match(model_id) or any(word in model_id for word in OPENAI_EXCLUDED):
        return False
    if capability == VISION:
        major = re.match(r"^gpt-(\d+)", model_id)
        return bool(OPENAI_VISION.match(model_id) or (major and int(major.group(1)) >= 5))
    return True


def _fetch_openai(api_key):
    response = get_http_session().get("https://api.openai.com/v1/models", timeout=REQUEST_TIMEOUT_SECONDS,
                                      headers={"Authorization": f"Bearer {api_key}"})
    response.raise_for_status()
    models = sorted(response.json()["data"], key=lambda m: m.get("created", 0), reverse=True)
    return [{"id": m["id"], "label": m["id"],
             "capabilities": [c for c in CAPABILITIES if _openai_supports(m["id"], c)]} for m in models]


def _fetch_claude(api_key):
    models, after_id = [], None
    while True:
        params = {"limit": 100, **({"after_id": after_id} if after_id else {})}
        response = get_http_session().get("https://api.anthropic.com/v1/models", params=params,
                                          timeout=REQUEST_TIMEOUT_SECONDS,
                                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        response.raise_for_status()
        data = response.json()
        models.extend(data["data"])
        if not data.get("has_more"):
            break
        after_id = data["last_id"]

    result = []
    for model in models:
        if model.get("lifecycle", "active") != "active":
            continue
        # Every current Claude model reads images, so a missing capabilities object counts as yes.
        image_input = ((model.get("capabilities") or {}).get("image_input") or {}).get("supported", True)
        result.append({"id": model["id"], "label": model.get("display_name") or model["id"],
                       "capabilities": [TEXT] + ([VISION] if image_input else [])})
    return result


def _fallback(provider):
    """The built-in model list, used when a provider's model list can't be fetched."""
    image_models = IMAGE_MODELS.get(provider, [])
    models = []
    for model_id in TEXT_MODELS.get(provider, []) + image_models:
        if model_id in image_models:
            capabilities = [IMAGE]
        elif provider == "claude":
            capabilities = [TEXT, VISION]
        else:
            capabilities = [c for c in (TEXT, VISION) if _openai_supports(model_id, c)]
        models.append({"id": model_id, "label": model_id, "capabilities": capabilities})
    return models


# Provider id, display name, .env key, and model list fetcher.
PROVIDERS = [
    ("claude", "Claude", "ANTHROPIC_API_KEY", _fetch_claude),
    ("openai", "OpenAI", "OPEN_AI_SECRET", _fetch_openai),
]


def _provider_models(provider, api_key, fetch, now):
    """Returns the provider's models, cached per API key so a new key is picked up right away."""
    cache_key = (provider, hashlib.sha256(api_key.encode()).hexdigest())
    cached = _cache.get(cache_key)
    if cached and now < cached[0]:
        return cached[1]
    try:
        models, expires = fetch(api_key), now + CACHE_SECONDS
    except Exception as e:
        logger.warning(f"Couldn't list {provider} models, using the built-in list: {e}")
        models, expires = _fallback(provider), now + FAILURE_CACHE_SECONDS
    _cache[cache_key] = (expires, models)
    return models


def get_model_groups(device_config, capability):
    """Returns [{"provider", "models": [{"id", "label"}]}] for each provider with an API key."""
    groups = []
    now = time.monotonic()
    for provider, name, env_key, fetch in PROVIDERS:
        api_key = device_config.load_env_key(env_key)
        if not api_key:
            continue
        models = [{"id": m["id"], "label": m["label"]}
                  for m in _provider_models(provider, api_key, fetch, now) if capability in m["capabilities"]]
        if models:
            groups.append({"provider": name, "models": models})
    return groups


def find_model(groups, model_id):
    """Returns the listed id for model_id, matching an alias like claude-haiku-4-5 to its dated id."""
    ids = [m["id"] for group in groups for m in group["models"]]
    if model_id in ids:
        return model_id
    return next((i for i in ids if model_id and i.startswith(f"{model_id}-")), None)


def model_picker(device_config, capability, value=None, default=None):
    """Returns what the ai_model_select macro shows: the groups, the selected id, and a saved id
    that isn't listed (kept so saving the form doesn't switch models silently)."""
    groups = get_model_groups(device_config, capability)
    selected = find_model(groups, value) if value else find_model(groups, default)
    unlisted = value if value and not selected else None
    if not selected and not unlisted and groups:
        selected = groups[0]["models"][0]["id"]
    return {"groups": groups, "selected": unlisted or selected, "unlisted": unlisted}
