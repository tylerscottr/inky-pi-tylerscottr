from abc import ABC, abstractmethod
import base64
import hashlib
import logging
from PIL import Image
from io import BytesIO
import re
import sys
from utils.http_client import post_with_retries

logger = logging.getLogger(__name__)

# The APIs are called directly; their SDKs would add about 48 MB of memory once loaded.
OPENAI_API = "https://api.openai.com/v1"
CLAUDE_API = "https://api.anthropic.com/v1"
CLAUDE_API_VERSION = "2023-06-01"
TEXT_TIMEOUT_SECONDS = 300
IMAGE_TIMEOUT_SECONDS = 300
QUOTA_EXHAUSTED = "insufficient_quota"

# Built-in model lists, used when a provider's model list can't be fetched (see utils/ai_models.py).
TEXT_MODELS = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
    ],
    "claude": [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-4-5",
        "claude-fable-5-1",
    ]
}

IMAGE_MODELS = {
    "openai": [
        "gpt-image-1",
        "gpt-image-2",
    ]
}

OPENAI_TEXT_PREFIX = re.compile(r"^(gpt-|chatgpt-|o\d)")
# GPT-5 and o-series models only accept the default temperature.
OPENAI_DEFAULT_TEMPERATURE = re.compile(r"^(gpt-5|o\d)")
CLAUDE_GENERATION = re.compile(r"^claude-(?:[a-z]+-)?(\d+)")


def claude_generation(model):
    """Returns the major version in a Claude model id, like 5 for claude-opus-5-5, or 0 if unknown."""
    match = CLAUDE_GENERATION.match(model)
    return int(match.group(1)) if match else 0


def claude_accepts_temperature(model):
    """Claude 5 models reject temperature with a 400."""
    return claude_generation(model) < 5


def claude_uses_fallback(model):
    """Claude 5 models' safety classifiers may decline, so they opt into server-side fallback."""
    return claude_generation(model) >= 5


def is_image_model(model):
    return model.startswith("gpt-image-")

class AITextGenerator(ABC):
    """Abstract base class for AI text generation services."""

    _cached_response = None
    _cache_key = None

    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def _make_text_request(self, model: str, system_prompt: str, user_prompt: str, temperature: float):
        """Make the actual API request to generate text."""
        pass

    def generate_text(self, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.5, use_cache: bool = None):
        """
        Generate text with optional caching for test environments.
        """
        if use_cache is None:
            use_cache = self._is_test_environment()

        if use_cache:
            cache_key = hashlib.md5(f"{model}:{system_prompt}:{user_prompt}".encode()).hexdigest()

            if (self._cached_response and self._cache_key == cache_key):
                logger.info(f"Using cached response for text generation (test mode)")
                return self._cached_response

        logger.info(f"Generating text with model: {model}")

        response = self._make_text_request(model, system_prompt, user_prompt, temperature)

        if use_cache:
            AITextGenerator._cached_response = response
            AITextGenerator._cache_key = cache_key
            logger.info(f"Cached response for next request (test mode)")

        return response

    @abstractmethod
    def _make_image_question_request(self, model: str, question: str, image_url: str):
        """Make the actual API request to answer a question about an image."""
        pass

    def answer_yes_no(self, model: str, question: str, image_url: str):
        """Asks a yes/no question about the image at image_url and returns True for yes."""
        answer = self._make_image_question_request(model, f"{question} Answer with only yes or no.", image_url)
        logger.info(f"{model} answered '{answer}' to: {question}")
        return answer.strip().lower().startswith("yes")

    def _is_test_environment(self):
        """Check if we're in a test environment."""
        return any('test_plugin.py' in arg for arg in sys.argv)


class AIImageGenerator(ABC):
    """Abstract base class for AI image generation services."""

    _cached_image = None
    _cache_key = None

    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def _make_image_request(self, model: str, prompt: str, size: str, quality: str):
        """Make the actual API request to generate an image."""
        pass

    def generate_image(self, model: str, prompt: str, size: str = "1024x1024", quality: str = "medium", use_cache: bool = None):
        """
        Generate image with optional caching for test environments.
        """
        if use_cache is None:
            use_cache = self._is_test_environment()

        if use_cache:
            cache_key = hashlib.md5(f"{model}:{prompt}:{size}:{quality}".encode()).hexdigest()

            if (self._cached_image and self._cache_key == cache_key):
                logger.info(f"Using cached image for generation (test mode)")
                return self._cached_image

        logger.info(f"Generating image with model: {model}, size: {size}")

        # Enhance prompt for better e-ink display results
        enhanced_prompt = self._enhance_prompt_for_eink(prompt)

        image = self._make_image_request(model, enhanced_prompt, size, quality)

        if use_cache:
            AIImageGenerator._cached_image = image
            AIImageGenerator._cache_key = cache_key
            logger.info(f"Cached image for next request (test mode)")

        return image

    def _enhance_prompt_for_eink(self, prompt: str):
        """Enhance prompt with e-ink display optimizations."""
        enhanced_prompt = prompt + (
            ". The image should fully occupy the entire canvas without any frames, "
            "borders, or cropped areas. No blank spaces or artificial framing."
        )
        enhanced_prompt += (
            " Focus on simplicity, bold shapes, and strong contrast to enhance clarity "
            "and visual appeal. Avoid excessive detail or complex gradients, ensuring "
            "the design works well with flat, vibrant colors."
        )
        return enhanced_prompt

    def _is_test_environment(self):
        """Check if we're in a test environment."""
        return any('test_plugin.py' in arg for arg in sys.argv)


def _post_json(url, headers, body, timeout, is_retryable=None):
    """POSTs a JSON API request and returns the reply, raising the API's own error message on failure."""
    response = post_with_retries(url, headers=headers, json=body, timeout=timeout, is_retryable=is_retryable)
    if not response.ok:
        raise RuntimeError(f"{url} returned {response.status_code}: {_api_error(response).get('message')}")
    return response.json()


def _api_error(response):
    """Returns the {"type", "code", "message"} error both APIs send, or the raw text as the message."""
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            return error
    except ValueError:
        pass
    return {"message": response.text[:500]}


def _openai_retryable(response):
    # OpenAI also answers an exhausted quota with 429, which waiting won't fix.
    error = _api_error(response)
    return QUOTA_EXHAUSTED not in (error.get("type"), error.get("code"))


class OpenAITextGenerator(AITextGenerator):
    """OpenAI implementation of text generation."""

    def _chat(self, model, messages, **options):
        reply = _post_json(f"{OPENAI_API}/chat/completions", {"Authorization": f"Bearer {self.api_key}"},
                           {"model": model, "messages": messages, **options}, TEXT_TIMEOUT_SECONDS,
                           is_retryable=_openai_retryable)
        return reply["choices"][0]["message"]["content"].strip()

    def _make_text_request(self, model: str, system_prompt: str, user_prompt: str, temperature: float):
        if OPENAI_DEFAULT_TEMPERATURE.match(model):
            openai_temperature = 1
        else:
            # Convert standardized 0.0-1.0 temperature scale to OpenAI's 0.0-2.0 scale
            openai_temperature = min(temperature * 2.0, 2.0)

        return self._chat(model, [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], temperature=openai_temperature)

    def _make_image_question_request(self, model: str, question: str, image_url: str):
        return self._chat(model, [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
        ]}])


class OpenAIImageGenerator(AIImageGenerator):
    """OpenAI implementation of image generation."""

    def _make_image_request(self, model: str, prompt: str, size: str = "1024x1024", quality: str = "medium"):
        reply = _post_json(f"{OPENAI_API}/images/generations", {"Authorization": f"Bearer {self.api_key}"},
                           {"model": model, "prompt": prompt, "size": size, "quality": quality},
                           IMAGE_TIMEOUT_SECONDS, is_retryable=_openai_retryable)
        image_bytes = base64.b64decode(reply["data"][0]["b64_json"])
        return Image.open(BytesIO(image_bytes))


class ClaudeTextGenerator(AITextGenerator):
    """Claude implementation of text generation."""

    def _make_text_request(self, model: str, system_prompt: str, user_prompt: str, temperature: float):
        return self._create_message(model, user_prompt, system=system_prompt, temperature=temperature)

    def _make_image_question_request(self, model: str, question: str, image_url: str):
        return self._create_message(model, [
            {"type": "image", "source": {"type": "url", "url": image_url}},
            {"type": "text", "text": question},
        ])

    def _create_message(self, model, content, system=None, temperature=None):
        headers = {"x-api-key": self.api_key, "anthropic-version": CLAUDE_API_VERSION}
        body = {"model": model, "max_tokens": 16000, "messages": [{"role": "user", "content": content}]}
        if system:
            body["system"] = system
        if temperature is not None and claude_accepts_temperature(model):
            body["temperature"] = temperature
        if claude_uses_fallback(model):
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"

        reply = _post_json(f"{CLAUDE_API}/messages", headers, body, TEXT_TIMEOUT_SECONDS)

        if reply.get("stop_reason") == "refusal":
            raise RuntimeError(f"Claude declined the request: {reply.get('stop_details')}")

        # Thinking blocks may precede the text, so find the text block explicitly.
        return next(block["text"] for block in reply["content"] if block["type"] == "text").strip()


def create_text_generator(model: str, device_config) -> AITextGenerator:
    """Factory function to create text generators based on model."""
    # Determine provider based on model
    if OPENAI_TEXT_PREFIX.match(model):
        api_key = device_config.load_env_key("OPEN_AI_SECRET")
        if not api_key:
            raise RuntimeError("OpenAI API Key (OPEN_AI_SECRET) not configured.")
        return OpenAITextGenerator(api_key)
    elif model.startswith(("claude-", "anthropic")):
        api_key = device_config.load_env_key("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Claude API Key (ANTHROPIC_API_KEY) not configured.")
        return ClaudeTextGenerator(api_key)
    else:
        raise ValueError(f"Unsupported text generation model: {model}")


def create_image_generator(model: str, device_config) -> AIImageGenerator:
    """Factory function to create image generators based on model."""
    # Currently only OpenAI models are supported for image generation
    if is_image_model(model):
        api_key = device_config.load_env_key("OPEN_AI_SECRET")
        if not api_key:
            raise RuntimeError("OpenAI API Key (OPEN_AI_SECRET) not configured.")
        return OpenAIImageGenerator(api_key)
    else:
        raise ValueError(f"Unsupported image generation model: {model}")
