from plugins.base_plugin.base_plugin import BasePlugin
from buttons import PLUGIN_ACTION_PREFIX
from utils.ai_utils import create_text_generator
from utils.image_loader import _is_low_resource_device
from utils.http_client import get_http_session
import json
import logging
import random
import requests

logger = logging.getLogger(__name__)

RANDOM_URL = "https://api.unsplash.com/photos/random"
SEARCH_URL = "https://api.unsplash.com/search/photos"
SEARCH_PAGE_SIZE = 30  # The search endpoint's maximum
RANDOM_COUNT = 5  # Spare photos to fall back on when one is hidden or fails the AI check

ACTION_HIDE = "hide_photo"
# The settings key holding the id of the photo the instance last rendered, for the hide action.
CURRENT_PHOTO_KEY = "_current_photo_id"

class Unsplash(BasePlugin):

    def get_button_actions(self):
        return [{"id": ACTION_HIDE, "label": "Hide this photo"}]

    def get_default_button_actions(self):
        return {"A": f"{PLUGIN_ACTION_PREFIX}{ACTION_HIDE}"}

    def run_button_action(self, action_id, settings, device_config):
        """Hides the photo on screen for good and shows a new one."""
        if action_id != ACTION_HIDE:
            return super().run_button_action(action_id, settings, device_config)

        photo_id = settings.get(CURRENT_PHOTO_KEY)
        if photo_id:
            logger.info(f"Hiding Unsplash photo {photo_id}.")
            self.save_hidden_photos(self.load_hidden_photos() | {photo_id})
        else:
            logger.warning("No Unsplash photo on record to hide, showing a new one.")
        return self.generate_image(settings, device_config)

    def get_hidden_photos_file(self):
        return self.get_plugin_dir("hidden_photos.json")

    def load_hidden_photos(self):
        try:
            with open(self.get_hidden_photos_file(), 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to read hidden Unsplash photos: {e}")
        return set()

    def save_hidden_photos(self, hidden):
        try:
            with open(self.get_hidden_photos_file(), 'w', encoding='utf-8') as f:
                json.dump(sorted(hidden), f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save hidden Unsplash photos: {e}")

    def _random_search_results(self, session, params):
        """Returns a random page of search results, shuffled, using page 1 to learn the page count."""
        params = {**params, 'per_page': SEARCH_PAGE_SIZE}
        response = session.get(SEARCH_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        results = data.get("results")
        if not results:
            logger.warning(f"No images found for search query: '{params['query']}'")
            raise RuntimeError("No images found for the given search query.")

        page = random.randint(1, data.get("total_pages") or 1)
        logger.info(f"Found {data.get('total')} images matching search query, using page {page}")
        if page > 1:
            response = session.get(SEARCH_URL, params={**params, 'page': page}, timeout=30)
            response.raise_for_status()
            # Pages near the reported end can come back empty.
            results = response.json().get("results") or results
        return random.sample(results, len(results))

    def _random_photos(self, session, params, search_query):
        response = session.get(RANDOM_URL, params={**params, 'count': RANDOM_COUNT}, timeout=30)
        if response.status_code == 404:
            logger.warning(f"No images found for search query: '{search_query}'")
            raise RuntimeError("No images found for the given search query.")
        response.raise_for_status()
        return response.json()

    def _choose_photo(self, candidates, settings, device_config, search_query):
        """Returns the first photo that isn't hidden and, when the AI check is on, matches the search."""
        hidden = self.load_hidden_photos()
        candidates = [photo for photo in candidates if photo["id"] not in hidden]
        if not candidates:
            raise RuntimeError("Every photo Unsplash returned is hidden, please try again.")
        if settings.get('aiCheck') != 'true' or not search_query:
            return candidates[0]

        model = settings.get('aiCheckModel')
        if not model:
            raise RuntimeError("Choose a model for the AI photo check.")
        question = f'Is this a good photo for an image search for "{search_query}"?'
        try:
            text_generator = create_text_generator(model, device_config)
            for photo in candidates:
                if text_generator.answer_yes_no(model, question, photo["urls"]["small"]):
                    return photo
                logger.info(f"AI check rejected photo {photo['id']}: {photo.get('alt_description')}")
        except Exception as e:
            logger.error(f"AI photo check failed: {e}")
            raise RuntimeError("AI photo check failed, please check logs.")
        raise RuntimeError(f"None of {len(candidates)} photos passed the AI check, will try again later.")

    def generate_image(self, settings, device_config):
        logger.info("=== Unsplash Plugin: Starting image generation ===")

        access_key = device_config.load_env_key("UNSPLASH_ACCESS_KEY")
        if not access_key:
            logger.error("Unsplash Access Key not found in environment")
            raise RuntimeError("'Unsplash Access Key' not found.")

        search_query = settings.get('search_query')
        collections = settings.get('collections')
        content_filter = settings.get('content_filter', 'low')
        color = settings.get('color')
        orientation = settings.get('orientation')

        # Automatically determine image size based on device capabilities
        is_low_resource = _is_low_resource_device()
        image_size = 'regular' if is_low_resource else 'full'
        logger.info(f"Device type: {'low-resource' if is_low_resource else 'standard'}, using image size: '{image_size}'")

        logger.info(f"Settings: image_size='{image_size}', content_filter='{content_filter}'")
        if search_query:
            logger.info(f"Search query: '{search_query}'")
        if collections:
            logger.info(f"Collections: {collections}")
        if color:
            logger.debug(f"Color filter: {color}")
        if orientation:
            logger.debug(f"Orientation: {orientation}")

        params = {
            'client_id': access_key,
            'content_filter': content_filter,
        }
        if search_query:
            params['query'] = search_query
        if collections:
            params['collections'] = collections
        if color:
            params['color'] = color
        if orientation:
            params['orientation'] = orientation

        try:
            logger.debug("Fetching image from Unsplash API...")
            session = get_http_session()
            # The random endpoint ignores color, and ignores the query when collections are set.
            if search_query and (color or collections):
                candidates = self._random_search_results(session, params)
            else:
                candidates = self._random_photos(session, params, search_query)
            photo = self._choose_photo(candidates, settings, device_config, search_query)
            # Use selected image size (with automatic downgrade for low-RAM devices)
            image_url = photo["urls"][image_size]

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching image from Unsplash API: {e}")
            raise RuntimeError("Failed to fetch image from Unsplash API, please check logs.")
        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing Unsplash API response: {e}")
            raise RuntimeError("Failed to parse Unsplash API response, please check logs.")


        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
            logger.debug(f"Vertical orientation detected, dimensions: {dimensions[0]}x{dimensions[1]}")

        logger.info(f"Fetching image (size: {image_size}): {image_url}")

        # Use adaptive image loader for memory-efficient processing
        image = self.image_loader.from_url(image_url, dimensions, timeout_ms=40000)

        if not image:
            logger.error("Failed to load and process image")
            raise RuntimeError("Failed to load image, please check logs.")

        # Under the config lock, since a web request may be saving these settings.
        with device_config.lock:
            settings[CURRENT_PHOTO_KEY] = photo["id"]
        logger.info("=== Unsplash Plugin: Image generation complete ===")
        return image
