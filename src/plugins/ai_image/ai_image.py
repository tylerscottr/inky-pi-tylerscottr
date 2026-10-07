from plugins.base_plugin.base_plugin import BasePlugin
from utils.ai_utils import create_text_generator, create_image_generator, ALL_IMAGE_MODELS
import logging

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_IMAGE_QUALITY = "medium"
PROMPT_MODEL = "gpt-4o"

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
        logger.info("=== AI Image Plugin: Starting image generation ===")

        text_prompt = settings.get("textPrompt", "")
        image_model = settings.get('imageModel', DEFAULT_IMAGE_MODEL)

        if image_model not in ALL_IMAGE_MODELS:
            logger.error(f"Invalid image model: {image_model}")
            raise RuntimeError("Invalid Image Model provided.")

        image_quality = settings.get('quality', DEFAULT_IMAGE_QUALITY)
        randomize_prompt = settings.get('randomizePrompt') == 'true'
        orientation = device_config.get_config("orientation")

        logger.info(f"Settings: model={image_model}, quality={image_quality}, orientation={orientation}")
        logger.debug(f"Original prompt: '{text_prompt}'")
        logger.debug(f"Randomize prompt: {randomize_prompt}")

        image = None
        try:
            image_generator = create_image_generator(image_model, device_config)

            if randomize_prompt:
                logger.debug(f"Generating randomized prompt using {PROMPT_MODEL}...")
                text_generator = create_text_generator(PROMPT_MODEL, device_config)
                text_prompt = AIImage.fetch_image_prompt(text_generator, text_prompt)
                logger.info(f"Randomized prompt: '{text_prompt}'")

            logger.info(f"Generating image with {image_model}...")
            image = self.fetch_image(
                image_generator,
                text_prompt,
                model=image_model,
                quality=image_quality,
                orientation=orientation
            )

            if image:
                logger.info(f"AI image generated successfully: {image.size[0]}x{image.size[1]}")

        except Exception as e:
            logger.error(f"Failed to make AI request: {str(e)}")
            raise RuntimeError("AI request failure, please check logs.")

        logger.info("=== AI Image Plugin: Image generation complete ===")
        return image

    def fetch_image(self, image_generator, prompt, model=DEFAULT_IMAGE_MODEL, quality=DEFAULT_IMAGE_QUALITY, orientation="horizontal"):
        logger.info(f"Generating image for prompt: {prompt}, model: {model}, quality: {quality}")
        size = "1536x1024" if orientation == "horizontal" else "1024x1536"
        return image_generator.generate_image(model, prompt, size, quality)

    @staticmethod
    def fetch_image_prompt(text_generator, from_prompt=None):
        logger.info("Getting random image prompt...")

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

        prompt = text_generator.generate_text(PROMPT_MODEL, system_content, user_content)
        logger.info(f"Generated random image prompt: {prompt}")
        return prompt
