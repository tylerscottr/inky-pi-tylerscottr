import json
import sys
import os
import logging.config
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging to see logger.info() messages
logging.config.fileConfig(os.path.join(os.path.dirname(__file__), '..', 'src', 'config', 'logging.conf'))

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
from plugins.plugin_registry import load_plugins, get_plugin_instance
from utils.image_utils import resize_image, change_orientation
from unittest.mock import patch, MagicMock
from PIL import Image, ImageDraw

PLUGIN_CONFIG_FILE = "install/config_base/plugins.json"
RESOLUTIONS = [
    [400, 300],	# Inky wHAT
    [640, 400], # Inky Impression 4"
    [600, 448], # Inky Impression 5.7"
    [800, 480], # Inky Impression 7.3"
]
ORIENTATIONS = ["horizontal", "vertical"]

plugin_id = "ai_text"
plugin_settings = {
    "title": "Today In History",
    "textModel": "gpt-5",
    "textPrompt": "Tell me something interesting.",
    "selectedFrame": "Rectangle"
}

<<<<<<< Updated upstream
mock_device_config = MagicMock()
mock_device_config.load_env_key.return_value = os.getenv("OPEN_AI_SECRET")
=======
def get_default_plugin_settings(plugin_id):
    """Get reasonable default settings for testing different plugins"""
    defaults = {
        "ai_text": {
            "title": "Test AI Text",
            "textModel": "gpt-4o-mini",
            "textPrompt": "Tell me something interesting.",
            "selectedFrame": "Rectangle"
        },
        "ai_image": {
            "imageModel": "dall-e-3",
            "textPrompt": "A futuristic cityscape at sunset.",
        },
        "weather": {
            "location": "Denver, CO",
            "latitude": 39.7392,
            "longitude": -104.9903,
            "units": "imperial",
            "displayGraph": "true",
            "displayRain": "true",
            "moonPhase": "true",
            "displayForecast": "true",
            "forecastDays": 7,
        },
        # "weather": {
        #     "weatherProvider": "OpenMeteo",
        #     "location": "Denver, CO",
        #     "latitude": 39.7392,
        #     "longitude": -104.9903,
        #     "units": "imperial",
        #     "displayGraph": "true",
        #     "displayRain": "true",
        #     "displayForecast": "true",
        #     "forecastDays": 7,
        # },
        "clock": {
            "timezone": "America/New_York",
            "format": "12h",
            "selectedClockFace": "Digital Clock",
            "primaryColor": "rgb(219,50,70)",
            "secondaryColor": "rgb(0,0,0)"
        },
        "calendar": {
            "calendarURLs[]": ["https://www.calendarlabs.com/ical-calendar/ics/76/US_Holidays.ics"],
            "calendarColors[]": ["rgb(219,50,70)"],
            "viewMode": "dayGridMonth"
        },
        "image_url": {
            "url": "https://picsum.photos/800/600",
        },
        "comic": {
            "comic": "XKCD",
        },
        "bible_quote": {
            "title": "Daily Scripture",
            "textModel": "gpt-4o-mini",
            "bibleVersion": "ESV"
        }
    }
    return defaults.get(plugin_id, {})
>>>>>>> Stashed changes

with open(PLUGIN_CONFIG_FILE) as f:
    plugins = json.load(f)

plugin_config = [config for config in plugins if config.get('id') == plugin_id]

if not plugin_config:
    exit(f"Plugin {plugin_id} not found in plugin config file: {PLUGIN_CONFIG_FILE}")

load_plugins(plugin_config)

plugin_config = plugin_config[0]
plugin_instance = get_plugin_instance(plugin_config)

total_height = sum([max(resolution) for resolution in RESOLUTIONS])
total_width = max([max(resolution) for resolution in RESOLUTIONS]) * 2

composite = Image.new('RGB', (total_width, total_height), color='gray')
y = 0
for resolution in RESOLUTIONS:
    x = 0
    width, height = resolution
    for orientation in ORIENTATIONS:
        mock_device_config.get_resolution.return_value = resolution
        mock_device_config.get_config.return_value = orientation

        try:
            img = plugin_instance.generate_image(plugin_settings, mock_device_config)
        except Exception as e:
            print(f"Plugin failed: {e}")
            # Create a placeholder image for testing
            img = Image.new('RGB', resolution, color='red')
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), "Plugin Error", fill='white')

        # post processing thats applied before being displayed
        img = change_orientation(img, orientation)
        img = resize_image(img, resolution, plugin_config.get('image_settings', []))
        # rotate the image again when pasting
        if orientation == "vertical":
            img = img.rotate(-90, expand=1)
        composite.paste(img, (x, y))
        x= int(total_width/2)
    y+= max(width, height)

# composite.show()
composite.save("composite_output.png")

