from plugins.base_plugin.base_plugin import BasePlugin
from openai import OpenAI
from PIL import Image
from io import BytesIO
import requests
import logging
import hashlib

logger = logging.getLogger(__name__)

IMAGE_MODELS = ["dall-e-3", "dall-e-2", "gpt-image-1"]
DEFAULT_IMAGE_MODEL = "dall-e-3"
DEFAULT_IMAGE_QUALITY = "standard"

class AIImage(BasePlugin):
    _cached_image = None
    _cached_prompt = None
    _image_cache_key = None
    _prompt_cache_key = None
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenAI",
            "expected_key": "OPEN_AI_SECRET"
        }
        return template_params

    def generate_image(self, settings, device_config):

        api_key = device_config.load_env_key("OPEN_AI_SECRET")
        if not api_key:
            raise RuntimeError("OPEN AI API Key not configured.")

        text_prompt = settings.get("textPrompt", "")

        image_model = settings.get('imageModel', DEFAULT_IMAGE_MODEL)
        if image_model not in IMAGE_MODELS:
            raise RuntimeError("Invalid Image Model provided.")
        image_quality = settings.get('quality', "medium" if image_model == "gpt-image-1" else "standard")
        randomize_prompt = settings.get('randomizePrompt') == 'true'

        image = None
        try:
            ai_client = OpenAI(api_key = api_key)
            if randomize_prompt:
                text_prompt = self.fetch_image_prompt(ai_client, text_prompt)

            image = self.fetch_image(
                ai_client,
                text_prompt,
                model=image_model,
                quality=image_quality,
                orientation=device_config.get_config("orientation")
            )
        except Exception as e:
            logger.error(f"Failed to make Open AI request: {str(e)}")
            raise RuntimeError("Open AI request failure, please check logs.")
        return image

    @classmethod
    def fetch_image(cls, ai_client, prompt, model="dalle-e-3", quality="standard", orientation="horizontal"):
        """
        Fetch image with caching for test environments only.
        In production (inkypi.py), no caching is applied to ensure fresh content.
        """
        # Only use caching if we're in a test environment (when test_plugin.py is running)
        import sys
        is_test_environment = any('test_plugin.py' in arg for arg in sys.argv)
        
        if is_test_environment:
            cache_key = hashlib.md5(f"{model}:{prompt}:{quality}".encode()).hexdigest()
            
            # Check if we have a cached response for this exact request
            if (cls._cached_image and cls._image_cache_key == cache_key):
                logger.info(f"Using cached image for prompt (test mode): {prompt}")
                return cls._cached_image

        logger.info(f"Generating image for prompt: {prompt}, model: {model}, quality: {quality}")
        prompt += (
            ". The image should fully occupy the entire canvas without any frames, "
            "borders, or cropped areas. No blank spaces or artificial framing."
        )
        prompt += (
            "Focus on simplicity, bold shapes, and strong contrast to enhance clarity "
            "and visual appeal. Avoid excessive detail or complex gradients, ensuring "
            "the design works well with flat, vibrant colors."
        )
        args = {
            "model": model,
            "prompt": prompt,
            "size": "1024x1024",
        }
        if model == "dall-e-3":
            args["size"] = "1792x1024" if orientation == "horizontal" else "1024x1792"
            args["quality"] = quality
        elif model == "gpt-image-1":
            args["size"] = "1536x1024" if orientation == "horizontal" else "1024x1536"
            args["quality"] = quality

        response = ai_client.images.generate(**args)
        image_url = response.data[0].url
        response = requests.get(image_url)
        img = Image.open(BytesIO(response.content))

        # Store in cache only if in test environment
        if is_test_environment:
            cls._cached_image = img
            cls._image_cache_key = cache_key
            logger.info(f"Cached image for next request (test mode)")

        return img

    @classmethod
    def fetch_image_prompt(cls, ai_client, from_prompt=None):
        """
        Fetch image prompt with caching for test environments only.
        In production (inkypi.py), no caching is applied to ensure fresh content.
        """
        # Only use caching if we're in a test environment (when test_plugin.py is running)
        import sys
        is_test_environment = any('test_plugin.py' in arg for arg in sys.argv)
        
        if is_test_environment:
            cache_key = hashlib.md5(f"{from_prompt or 'random'}".encode()).hexdigest()
            
            # Check if we have a cached response for this exact request
            if (cls._cached_prompt and cls._prompt_cache_key == cache_key):
                logger.info(f"Using cached prompt for request (test mode): {from_prompt or 'random'}")
                return cls._cached_prompt

        logger.info(f"Getting random image prompt...")

        system_content = (
            "You are a creative assistant generating extremely random and unique image prompts. "
            "Avoid common themes. Focus on unexpected, unconventional, and bizarre combinations "
            "of art style, medium, subjects, time periods, and moods. No repetition. Prompts "
            "should be 20 words or less and specify random artist, movie, tv show or time period "
            "for the theme. Do not provide any headers or repeat the request, just provide the "
            "updated prompt in your response."
        )
        user_content = (
            "Give me a completely random image prompt, something unexpected and creative! "
            "Let's see what your AI mind can cook up!"
        )
        if from_prompt and from_prompt.strip():
            system_content = (
                "You are a creative assistant specializing in generating highly descriptive "
                "and unique prompts for creating images. When given a short or simple image "
                "description, your job is to rewrite it into a more detailed, imaginative, "
                "and descriptive version that captures the essence of the original while "
                "making it unique and vivid. Avoid adding irrelevant details but feel free "
                "to include creative and visual enhancements. Avoid common themes. Focus on "
                "unexpected, unconventional, and bizarre combinations of art style, medium, "
                "subjects, time periods, and moods. Do not provide any headers or repeat the "
                "request, just provide your updated prompt in the response. Prompts "
                "should be 20 words or less and specify random artist, movie, tv show or time "
                "period for the theme."
            )
            user_content = (
                f"Original prompt: \"{from_prompt}\"\n"
                "Rewrite it to make it more detailed, imaginative, and unique while staying "
                "true to the original idea. Include vivid imagery and descriptive details. "
                "Avoid changing the subject of the prompt."
            )

        # Make the API call
        response = ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            temperature=1
        )

        prompt = response.choices[0].message.content.strip()
        logger.info(f"Generated random image prompt: {prompt}")
        
        # Store in cache only if in test environment
        if is_test_environment:
            cls._cached_prompt = prompt
            cls._prompt_cache_key = cache_key
            logger.info(f"Cached prompt for next request (test mode)")
        
        return prompt
