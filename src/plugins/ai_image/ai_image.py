from plugins.base_plugin.base_plugin import BasePlugin
from utils.ai_utils import create_text_generator, create_image_generator, ALL_IMAGE_MODELS
from PIL import Image
import logging

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "dall-e-3"
DEFAULT_IMAGE_QUALITY = "standard"

class AIImage(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenAI",
            "expected_key": "OPEN_AI_SECRET"
        }
        return template_params

    def generate_image(self, settings, device_config):
        text_prompt = settings.get("textPrompt", "")

        image_model = settings.get('imageModel', DEFAULT_IMAGE_MODEL)
        if image_model not in ALL_IMAGE_MODELS:
            raise RuntimeError("Invalid Image Model provided.")
        image_quality = settings.get('quality', "medium" if image_model == "gpt-image-1" else "standard")
        randomize_prompt = settings.get('randomizePrompt') == 'true'

        image = None
        try:
            image_generator = create_image_generator(image_model, device_config)
            
            if randomize_prompt:
                text_generator = create_text_generator("gpt-4o", device_config)
                text_prompt = self.fetch_image_prompt(text_generator, text_prompt)

            image = self.fetch_image(
                image_generator,
                text_prompt,
                model=image_model,
                quality=image_quality,
                orientation=device_config.get_config("orientation")
            )
        except Exception as e:
            logger.error(f"Failed to make AI request: {str(e)}")
            raise RuntimeError("AI request failure, please check logs.")
        return image

    @classmethod
    def fetch_image(cls, image_generator, prompt, model="dall-e-3", quality="standard", orientation="horizontal"):
        """
        Fetch image using the AI utility class.
        """
        logger.info(f"Generating image for prompt: {prompt}, model: {model}, quality: {quality}")
        
        # Determine size based on model and orientation
        size = "1024x1024"
        if model == "dall-e-3":
            size = "1792x1024" if orientation == "horizontal" else "1024x1792"
        elif model == "gpt-image-1":
            size = "1536x1024" if orientation == "horizontal" else "1024x1536"

        return image_generator.generate_image(model, prompt, size, quality)

    @classmethod
    def fetch_image_prompt(cls, text_generator, from_prompt=None):
        """
        Fetch image prompt using the AI utility class.
        """
        logger.info(f"Getting image prompt...")
        
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
        else:
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

        prompt = text_generator.generate_text("gpt-4o", system_content, user_content)
        logger.info(f"Generated image prompt: {prompt}")
        
        return prompt
