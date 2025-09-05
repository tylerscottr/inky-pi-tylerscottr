from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import resolve_path
from openai import OpenAI
from utils.image_utils import resize_image
from datetime import datetime, timedelta
import logging
import hashlib
import json
import os

logger = logging.getLogger(__name__)

class BibleQuote(BasePlugin):
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
            raise RuntimeError("OpenAI API Key not configured.")

        title = settings.get("title", "Daily Scripture") or "Daily Scripture"

        text_model = settings.get('textModel')
        if not text_model:
            raise RuntimeError("Text Model is required.")

        bible_version = settings.get('bibleVersion', 'ESV')

        try:
            ai_client = OpenAI(api_key=api_key)
            used_quotes = self.get_used_quotes()
            logger.info(f"Retrieved {len(used_quotes)} previously used quotes: {used_quotes}")
            bible_response = self.fetch_daily_scripture(ai_client, text_model, bible_version, used_quotes)
        except Exception as e:
            logger.error(f"Failed to make OpenAI request: {str(e)}")
            raise RuntimeError("OpenAI request failure, please check logs.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        # Parse the JSON response
        try:
            # Remove markdown code blocks if present
            json_response = bible_response.strip()
            if json_response.startswith('```json'):
                json_response = json_response[7:]  # Remove ```json
            if json_response.endswith('```'):
                json_response = json_response[:-3]  # Remove ```
            json_response = json_response.strip()
            
            bible_data = json.loads(json_response)
            text = bible_data.get('text', '')
            source = bible_data.get('source', '')
            reason = bible_data.get('reason', '')
            
            # Save successful quote to file
            if source:  # Only save if we have a valid source
                self.save_used_quote(source)
                
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {bible_response}")
            # Fallback to treating the response as plain text
            text = bible_response
            source = ""
            reason = ""

        image_template_params = {
            "title": title,
            "text": text,
            "source": source,
            "reason": reason,
            "bible_icon": self.get_plugin_dir('icons/color-icon.svg'),
            "plugin_settings": settings
        }
        
        image = self.render_image(dimensions, "daily_scripture.html", "daily_scripture.css", image_template_params)

        return image
    
    def get_used_quotes_file(self):
        """Get the path to the used quotes file."""
        plugin_dir = self.get_plugin_dir()
        return os.path.join(plugin_dir, 'used_quotes.txt')
    
    def save_used_quote(self, source):
        """Save a used quote source to the file, maintaining max 30 entries."""
        quotes_file = self.get_used_quotes_file()
        
        try:
            # Read existing quotes
            if os.path.exists(quotes_file):
                with open(quotes_file, 'r', encoding='utf-8') as f:
                    used_quotes = [line.strip() for line in f.readlines() if line.strip()]
            else:
                used_quotes = []
            
            # Add new quote if not already present
            if source not in used_quotes:
                used_quotes.append(source)
                
                # Keep only the last 30 entries
                if len(used_quotes) > 30:
                    used_quotes = used_quotes[-30:]
                
                # Write back to file (creates file if it doesn't exist)
                with open(quotes_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(used_quotes) + '\n')
                
                logger.info(f"Saved used quote: {source}")
            
        except Exception as e:
            logger.warning(f"Failed to save used quote: {e}")
    
    def get_used_quotes(self):
        """Get list of previously used quote sources."""
        quotes_file = self.get_used_quotes_file()
        
        try:
            if os.path.exists(quotes_file):
                with open(quotes_file, 'r', encoding='utf-8') as f:
                    return [line.strip() for line in f.readlines() if line.strip()]
        except Exception as e:
            logger.warning(f"Failed to read used quotes: {e}")
        
        return []
    
    @classmethod
    def fetch_daily_scripture(cls, ai_client, model, bible_version, used_quotes=None):
        """
        Fetch Bible quote with caching for test environments only.
        In production (inkypi.py), no caching is applied to ensure fresh content.
        """
        # Only use caching if we're in a test environment (when test_plugin.py is running)
        import sys
        is_test_environment = any('test_plugin.py' in arg for arg in sys.argv)
        
        if is_test_environment:
            cache_key = hashlib.md5(f"{model}:{bible_version}".encode()).hexdigest()
            current_time = datetime.now()
            
            # Check if we have a cached response for this exact prompt that's less than 1 minute old
            if (cls._cached_response and cls._cache_key == cache_key):
                logger.info(f"Using cached response for Bible quote (test mode): {bible_version}")
                return cls._cached_response

        logger.info(f"Getting Bible quote, version: {bible_version}, model: {model}")

        today_date = datetime.today().strftime('%Y-%m-%d')
        
        # Build the system prompt with used quotes avoidance
        system_content = (
            "You are a knowledgeable Bible scholar. Provide a Bible quote that shows assurance of pardon, "
            "or is convicting, inspiring, encouraging, or hopeful. "
            "If Christmas, Easter, or Passover is currently happening or will happen in the next 2 weeks, "
            "choose a quote related to that holiday. If there is another Christian holiday in the next 3 days, "
            "choose a quote related to that holiday. "
        )
        
        system_content += (
            "Format your response as valid JSON with the following fields: "
            "text: The text from the Bible passage (keep it concise, preferably under 100 words), "
            "source: The location of that passage in the Bible formatted as '<book> <chapter>:<verse_start>[-<verse_end>] (<bible_version>)', "
            "reason: A short description (1-2 sentences) of why this passage is relevant, inspirational, hopeful, or redeeming. "
            f"For reference, today is {today_date}. "
        )
        
        user_content = f"Please provide a Bible quote using the {bible_version} version of the Bible."

        # Add used quotes avoidance if we have previous quotes
        if used_quotes:
            logger.info(f"Instructing AI to avoid {len(used_quotes)} previously used passages")
            user_content += (
                f"IMPORTANT: Avoid providing any of these previously used Bible passages: {', '.join(used_quotes)}. "
                "Please select a different passage that has not been used recently. "
            )

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
            temperature=0.7
        )

        quote_response = response.choices[0].message.content.strip()
        logger.info(f"Generated Bible quote response: {quote_response}")
        
        # Store in cache only if in test environment
        if is_test_environment:
            cls._cached_response = quote_response
            cls._cache_key = cache_key
            logger.info(f"Cached response for next request (test mode)")

        return quote_response