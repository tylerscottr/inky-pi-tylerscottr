import json
import sys
import os
import argparse
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

def discover_available_plugins():
    """Discover all available plugins from their plugin-info.json files"""
    plugins_dir = os.path.join(os.path.dirname(__file__), '..', 'src', 'plugins')
    available_plugins = {}
    
    for plugin_dir in os.listdir(plugins_dir):
        plugin_path = os.path.join(plugins_dir, plugin_dir)
        if os.path.isdir(plugin_path):
            plugin_info_path = os.path.join(plugin_path, 'plugin-info.json')
            if os.path.exists(plugin_info_path):
                try:
                    with open(plugin_info_path) as f:
                        plugin_info = json.load(f)
                    available_plugins[plugin_info['id']] = {
                        'display_name': plugin_info['display_name'],
                        'class': plugin_info['class']
                    }
                except Exception as e:
                    print(f"Error reading plugin info for {plugin_dir}: {e}")
    
    return available_plugins

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
        "daily_scripture": {
            "textModel": "gpt-4o-mini",
            "selectedFrame": "Rectangle",
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
    }
    return defaults.get(plugin_id, {})

def main():
    parser = argparse.ArgumentParser(description='Test InkyPi plugins')
    parser.add_argument('plugin_id', nargs='?', help='Plugin ID to test (optional)')
    parser.add_argument('--list', action='store_true', help='List available plugins')
    parser.add_argument('--output', default='composite_output.png', help='Output file name')
    parser.add_argument('--resolution', type=str, help='Single resolution to test (e.g., "400x300")')
    parser.add_argument('--orientation', choices=['horizontal', 'vertical'], help='Single orientation to test')
    args = parser.parse_args()

    available_plugins = discover_available_plugins()
    
    if args.list:
        print("Available plugins:")
        for plugin_id, info in available_plugins.items():
            print(f"  {plugin_id}: {info['display_name']}")
        return

    # Select plugin
    if args.plugin_id:
        if args.plugin_id not in available_plugins:
            print(f"Plugin '{args.plugin_id}' not found. Available plugins:")
            for plugin_id, info in available_plugins.items():
                print(f"  {plugin_id}: {info['display_name']}")
            sys.exit(1)
        plugin_id = args.plugin_id
        plugin_settings = get_default_plugin_settings(plugin_id)
    else:
        print("Available plugins:")
        for i, (pid, info) in enumerate(available_plugins.items(), 1):
            print(f"{i}. {pid}: {info['display_name']}")
        
        try:
            choice = int(input("\nEnter plugin number to test: ")) - 1
            plugin_id = list(available_plugins.keys())[choice]
            plugin_settings = get_default_plugin_settings(plugin_id)
        except (ValueError, IndexError, KeyboardInterrupt):
            print("Invalid selection or cancelled")
            sys.exit(1)

    # Select resolution(s)
    if args.resolution:
        try:
            width, height = map(int, args.resolution.split('x'))
            if [width, height] not in RESOLUTIONS:
                print(f"Resolution {args.resolution} not in available resolutions: {RESOLUTIONS}")
                sys.exit(1)
            test_resolutions = [[width, height]]
        except ValueError:
            print(f"Invalid resolution format: {args.resolution}. Use format like '400x300'")
            sys.exit(1)
    else:
        print("\nAvailable resolutions:")
        for i, res in enumerate(RESOLUTIONS, 1):
            print(f"{i}. {res[0]}x{res[1]}")
        print(f"{len(RESOLUTIONS)+1}. All resolutions")
        
        try:
            choice = int(input("\nEnter resolution number to test: ")) - 1
            if choice == len(RESOLUTIONS):
                test_resolutions = RESOLUTIONS
            else:
                test_resolutions = [RESOLUTIONS[choice]]
        except (ValueError, IndexError, KeyboardInterrupt):
            print("Invalid selection or cancelled")
            sys.exit(1)

    # Select orientation(s)
    if args.orientation:
        test_orientations = [args.orientation]
    else:
        print("\nAvailable orientations:")
        for i, orientation in enumerate(ORIENTATIONS, 1):
            print(f"{i}. {orientation}")
        print(f"{len(ORIENTATIONS)+1}. Both orientations")
        
        try:
            choice = int(input("\nEnter orientation number to test: ")) - 1
            if choice == len(ORIENTATIONS):
                test_orientations = ORIENTATIONS
            else:
                test_orientations = [ORIENTATIONS[choice]]
        except (ValueError, IndexError, KeyboardInterrupt):
            print("Invalid selection or cancelled")
            sys.exit(1)

    print(f"\nTesting plugin: {plugin_id}")
    print(f"Resolutions: {[f'{r[0]}x{r[1]}' for r in test_resolutions]}")
    print(f"Orientations: {test_orientations}")
    print(f"Using settings: {json.dumps(plugin_settings, indent=2)}")

    # Create interleaved resolution set with transposed versions if both orientations selected
    both_orientations = len(test_orientations) == 2

    mock_device_config = MagicMock()
    mock_device_config.load_env_key.side_effect = lambda key: os.getenv(key)

    plugin_config = {
        'id': plugin_id,
        'display_name': available_plugins[plugin_id]['display_name'],
        'class': available_plugins[plugin_id]['class']
    }

    load_plugins([plugin_config])
    plugin_instance = get_plugin_instance(plugin_config)

    # Calculate composite dimensions based on interleaved resolutions
    if both_orientations:
        # Two columns: original and transposed resolutions
        max_res_width = max([resolution[0] for resolution in test_resolutions])
        total_width = max([sum(resolution) for resolution in test_resolutions])
        total_height = sum([max(resolution) for resolution in test_resolutions])
    else:
        # Single column
        total_width = max([resolution[0] for resolution in test_resolutions])
        total_height = sum([resolution[1] for resolution in test_resolutions])
        
        # If vertical orientation, swap total_width/total_height
        if test_orientations[0] == "vertical":
            total_width, total_height = total_height, total_width

    composite = Image.new('RGB', (total_width, total_height), color='gray')
    print("Generating composite image ({}x{})...".format(total_width, total_height))
    y = 0
    
    if both_orientations:
        # Process pairs: original + transposed for each base resolution
        for resolution in test_resolutions:
            x = 0
            width, height = resolution

            for orientation in test_orientations:
                mock_device_config.get_resolution.return_value = resolution
                mock_device_config.get_config.side_effect = lambda key, default=None: {
                    'orientation': orientation,
                    'timezone': 'America/New_York',
                    'time_format': '12h'
                }.get(key, default)

                try:
                    img = plugin_instance.generate_image(plugin_settings, mock_device_config)
                    print(f"✓ Generated image for {width}x{height} {orientation}")
                except Exception as e:
                    print(f"✗ Plugin failed for {width}x{height} {orientation}: {e}")
                    img = Image.new('RGB', resolution, color='red')
                    draw = ImageDraw.Draw(img)
                    draw.text((10, 10), "Plugin Error", fill='white')

                img = change_orientation(img, orientation)
                img = resize_image(img, resolution, plugin_config.get('image_settings', []))
                if orientation == "vertical":
                    img = img.rotate(-90, expand=1)
                
                composite.paste(img, (x, y))
                x = max_res_width
            
            y += max(width, height)
    else:
        # Single orientation: process each resolution individually
        for resolution in test_resolutions:
            width, height = resolution
            orientation = test_orientations[0]
            
            mock_device_config.get_resolution.return_value = resolution
            mock_device_config.get_config.side_effect = lambda key, default=None: {
                'orientation': orientation,
                'timezone': 'America/New_York',
                'time_format': '12h'
            }.get(key, default)

            try:
                img = plugin_instance.generate_image(plugin_settings, mock_device_config)
                print(f"✓ Generated image for {width}x{height} {orientation}")
            except Exception as e:
                print(f"✗ Plugin failed for {width}x{height} {orientation}: {e}")
                img = Image.new('RGB', resolution, color='red')
                draw = ImageDraw.Draw(img)
                draw.text((10, 10), "Plugin Error", fill='white')

            img = change_orientation(img, orientation)
            img = resize_image(img, resolution, plugin_config.get('image_settings', []))
            if orientation == "vertical":
                img = img.rotate(-90, expand=1)
            
            composite.paste(img, (0, y))
            y += max(width, height)

    composite.save(args.output)
    print(f"\nComposite image saved as: {args.output}")
    composite.show()

if __name__ == "__main__":
    main()

