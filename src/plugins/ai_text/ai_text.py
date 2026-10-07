from plugins.base_plugin.base_plugin import BasePlugin
from utils.ai_utils import create_text_generator, get_text_models_by_provider
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class AIText(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenAI or Anthropic",
            "expected_key": "OPEN_AI_SECRET or ANTHROPIC_API_KEY"
        }
        template_params['style_settings'] = True
        template_params['ai_text_models_by_provider'] = get_text_models_by_provider()
        return template_params

    def generate_image(self, settings, device_config):
        title = settings.get("title")

        text_model = settings.get('textModel')
        if not text_model:
            raise RuntimeError("Text Model is required.")

        text_prompt = settings.get('textPrompt', '')
        if not text_prompt.strip():
            raise RuntimeError("Text Prompt is required.")

        try:
            text_generator = create_text_generator(text_model, device_config)
            prompt_response = AIText.fetch_text_prompt(text_generator, text_model, text_prompt)
        except Exception as e:
            logger.error(f"Failed to make AI request: {str(e)}")
            raise RuntimeError("AI request failure, please check logs.")

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

    @staticmethod
    def fetch_text_prompt(text_generator, model, text_prompt):
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

        prompt = text_generator.generate_text(model, system_content, text_prompt)
        logger.info(f"Generated random text prompt: {prompt}")
        return prompt
