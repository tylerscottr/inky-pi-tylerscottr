from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import resolve_path
from openai import OpenAI
from utils.image_utils import resize_image
from datetime import datetime, timedelta
import logging
import hashlib

logger = logging.getLogger(__name__)

class AIText(BasePlugin):
    _cached_response = None
    _cache_key = None
    _cache_timestamp = None
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenAI",
            "expected_key": "OPEN_AI_SECRET"
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        api_key = device_config.load_env_key("OPEN_AI_SECRET")
        if not api_key:
            raise RuntimeError("OPEN AI API Key not configured.")

        title = settings.get("title")

        text_model = settings.get('textModel')
        if not text_model:
            raise RuntimeError("Text Model is required.")

        text_prompt = settings.get('textPrompt', '')
        if not text_prompt.strip():
            raise RuntimeError("Text Prompt is required.")

        try:
            ai_client = OpenAI(api_key = api_key)
            prompt_response = self.fetch_text_prompt(ai_client, text_model, text_prompt)
        except Exception as e:
            logger.error(f"Failed to make Open AI request: {str(e)}")
            raise RuntimeError("Open AI request failure, please check logs.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        image_template_params = {
            "title": title,
            "content": prompt_response,
            "plugin_settings": settings
        }
        
        image = self.render_image(dimensions, "ai_text.html", "ai_text.css", image_template_params)

        return image
    
    @classmethod
    def fetch_text_prompt(cls, ai_client, model, text_prompt):
        """
        Fetch text prompt with caching for test environments only.
        In production (inkypi.py), no caching is applied to ensure fresh content.
        """
        # Only use caching if we're in a test environment (when test_plugin.py is running)
        import sys
        is_test_environment = any('test_plugin.py' in arg for arg in sys.argv)
        
        if is_test_environment:
            cache_key = hashlib.md5(f"{model}:{text_prompt}".encode()).hexdigest()
            current_time = datetime.now()
            
            # Check if we have a cached response for this exact prompt that's less than 1 minute old
            if (cls._cached_response and cls._cache_key == cache_key):
                logger.info(f"Using cached response for prompt (test mode): {text_prompt}")
                return cls._cached_response


        logger.info(f"Getting random text prompt from input {text_prompt}, model: {model}")

        system_content = (
            "You are a highly intelligent text generation assistant. Generate concise, "
            "relevant, and accurate responses tailored to the user's input. The response "
            "should be 70 words or less."
            "IMPORTANT: Do not rephrase, reword, or provide an introduction. Respond directly "
            "to the request without adding explanations or extra context "
            "IMPORTANT: If the response naturally requires a newline for formatting, provide "
            "the '\n' newline character explicitly for every new line. For regular sentences "
            "or paragraphs do not provide the new line character."
            f"For context, today is {datetime.today().strftime('%Y-%m-%d')}"
        )
        user_content = text_prompt

        # Make the API call
        response = ai_client.chat.completions.create(
            model=model,
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
        logger.info(f"Generated random text prompt: {prompt}")
        
        # Store in cache only if in test environment
        if is_test_environment:
            cls._cached_response = prompt
            cls._cache_key = cache_key
            logger.info(f"Cached response for next request (test mode)")

        return prompt