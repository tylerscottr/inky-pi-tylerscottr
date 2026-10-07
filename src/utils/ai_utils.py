from abc import ABC, abstractmethod
import base64
import hashlib
import logging
from PIL import Image
from io import BytesIO
import sys

logger = logging.getLogger(__name__)

# Available AI models for text generation
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

# Available AI models for image generation
IMAGE_MODELS = {
    "openai": [
        "gpt-image-1",
        "gpt-image-2",
    ]
}

# Claude models that accept temperature; newer models reject it with a 400.
CLAUDE_SAMPLING_MODELS = {"claude-haiku-4-5"}

# Claude models whose safety classifiers may decline; these opt into server-side fallback.
CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}

# Flatten model lists for easy access
ALL_TEXT_MODELS = []
for provider_models in TEXT_MODELS.values():
    ALL_TEXT_MODELS.extend(provider_models)

ALL_IMAGE_MODELS = []
for provider_models in IMAGE_MODELS.values():
    ALL_IMAGE_MODELS.extend(provider_models)


def get_text_models():
    """Get all available text generation models."""
    return ALL_TEXT_MODELS.copy()


def get_image_models():
    """Get all available image generation models."""
    return ALL_IMAGE_MODELS.copy()


def get_text_models_by_provider():
    """Get text models organized by provider."""
    return TEXT_MODELS.copy()


def get_image_models_by_provider():
    """Get image models organized by provider."""
    return IMAGE_MODELS.copy()

class AITextGenerator(ABC):
    """Abstract base class for AI text generation services."""

    _cached_response = None
    _cache_key = None

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._initialize_client()

    @abstractmethod
    def _initialize_client(self):
        """Initialize the AI client."""
        pass

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

    def _is_test_environment(self):
        """Check if we're in a test environment."""
        return any('test_plugin.py' in arg for arg in sys.argv)


class AIImageGenerator(ABC):
    """Abstract base class for AI image generation services."""

    _cached_image = None
    _cache_key = None

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._initialize_client()

    @abstractmethod
    def _initialize_client(self):
        """Initialize the AI client."""
        pass

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


class OpenAITextGenerator(AITextGenerator):
    """OpenAI implementation of text generation."""

    def _initialize_client(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key)

    def _make_text_request(self, model: str, system_prompt: str, user_prompt: str, temperature: float):
        # GPT-5 models only accept the default temperature of 1.
        if model.startswith("gpt-5"):
            openai_temperature = 1
        else:
            # Convert standardized 0.0-1.0 temperature scale to OpenAI's 0.0-2.0 scale
            openai_temperature = min(temperature * 2.0, 2.0)

        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=openai_temperature
        )
        return response.choices[0].message.content.strip()


class OpenAIImageGenerator(AIImageGenerator):
    """OpenAI implementation of image generation."""

    def _initialize_client(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key)

    def _make_image_request(self, model: str, prompt: str, size: str = "1024x1024", quality: str = "medium"):
        response = self.client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
        )
        image_bytes = base64.b64decode(response.data[0].b64_json)
        return Image.open(BytesIO(image_bytes))


class ClaudeTextGenerator(AITextGenerator):
    """Claude implementation of text generation."""

    def _initialize_client(self):
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            raise RuntimeError("anthropic package is required for Claude support. Install with: pip install anthropic")

    def _make_text_request(self, model: str, system_prompt: str, user_prompt: str, temperature: float):
        options = {}
        if model in CLAUDE_SAMPLING_MODELS:
            # The 1.x SDK has no temperature argument, so send it as a raw body field.
            options["extra_body"] = {"temperature": temperature}
        if model in CLAUDE_FALLBACK_MODELS:
            options["betas"] = ["server-side-fallback-2026-07-01"]
            options["fallbacks"] = "default"

        response = self.client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ],
            **options
        )

        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined the request: {response.stop_details}")

        # Thinking blocks may precede the text, so find the text block explicitly.
        return next(block.text for block in response.content if block.type == "text").strip()


def create_text_generator(model: str, device_config) -> AITextGenerator:
    """Factory function to create text generators based on model."""
    # Determine provider based on model
    if model.startswith(("gpt-", "o1-", "chatgpt-")):
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
    if model.startswith("gpt-image-"):
        api_key = device_config.load_env_key("OPEN_AI_SECRET")
        if not api_key:
            raise RuntimeError("OpenAI API Key (OPEN_AI_SECRET) not configured.")
        return OpenAIImageGenerator(api_key)
    else:
        raise ValueError(f"Unsupported image generation model: {model}")
