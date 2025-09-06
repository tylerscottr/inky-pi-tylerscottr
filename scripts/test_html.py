#!/usr/bin/env python3
"""
Test HTML Server - Serves inky.html template locally similar to Raspberry Pi
"""

import os
import sys
import json
import logging
from flask import Flask, render_template, url_for, send_from_directory, Blueprint
from jinja2 import FileSystemLoader, ChoiceLoader

# Add src directory to path to import modules FIRST
script_dir = os.path.dirname(__file__)
project_root = os.path.join(script_dir, '..')
src_dir = os.path.join(project_root, 'src')
sys.path.insert(0, src_dir)

from utils.ai_utils import get_text_models, get_image_models, get_text_models_by_provider, get_image_models_by_provider

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from config import Config

# Use absolute paths for templates and static files
templates_dir = os.path.join(src_dir, 'templates')
static_dir = os.path.join(src_dir, 'static')
plugins_dir = os.path.join(src_dir, 'plugins')

app = Flask(__name__, 
           template_folder=templates_dir, 
           static_folder=static_dir)

# Set up custom template loader to search multiple directories
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(templates_dir),
    FileSystemLoader(plugins_dir)
])

# Add template filter for relative time formatting
@app.template_filter('format_relative_time')
def format_relative_time(timestamp_str):
    """Convert timestamp string to relative time format"""
    try:
        from datetime import datetime, timezone
        timestamp = datetime.fromisoformat(timestamp_str.replace(' ', 'T'))
        now = datetime.now(timezone.utc)
        diff = now - timestamp.replace(tzinfo=timezone.utc)
        
        minutes = int(diff.total_seconds() / 60)
        if minutes < 1:
            return "just now"
        elif minutes < 60:
            return f"{minutes} min ago"
        else:
            hours = int(minutes / 60)
            return f"{hours} hr ago"
    except Exception:
        return "recently"

# Create blueprints to match the actual app structure
settings_bp = Blueprint('settings', __name__)
plugin_bp = Blueprint('plugin', __name__)
playlist_bp = Blueprint('playlist', __name__)

class MockPlugin:
    """Mock plugin class to simulate plugin structure"""
    def __init__(self, plugin_id, display_name):
        self.id = plugin_id
        self.display_name = display_name

def create_mock_plugins():
    """Create mock plugins based on actual plugin directories"""
    plugins = []
    
    if os.path.exists(plugins_dir):
        for plugin_dir in os.listdir(plugins_dir):
            plugin_path = os.path.join(plugins_dir, plugin_dir)
            if os.path.isdir(plugin_path) and plugin_dir != '__pycache__':
                plugin_info_file = os.path.join(plugin_path, 'plugin-info.json')
                if os.path.isfile(plugin_info_file):
                    try:
                        with open(plugin_info_file, 'r') as f:
                            plugin_info = json.load(f)
                        plugins.append(MockPlugin(
                            plugin_id=plugin_dir,
                            display_name=plugin_info.get('display_name', plugin_dir)
                        ))
                    except (json.JSONDecodeError, KeyError):
                        # Fallback for plugins without proper info
                        plugins.append(MockPlugin(
                            plugin_id=plugin_dir,
                            display_name=plugin_dir.title()
                        ))
    
    # Add some default mock plugins if none found
    if not plugins:
        plugins = [
            MockPlugin('weather', 'Weather'),
            MockPlugin('calendar', 'Calendar'),
            MockPlugin('news', 'News'),
            MockPlugin('system_info', 'System Info')
        ]
    
    return plugins

def load_plugin_template_params(plugin_id, plugin_info):
    """Load template parameters from the actual plugin by instantiating it"""
    try:
        # Import the plugin module dynamically
        plugin_module_name = plugin_info.get('main_module', plugin_id)
        plugin_class_name = plugin_info.get('class_name', plugin_id.title())
        
        # Try to import and instantiate the plugin
        import importlib.util
        plugin_file = os.path.join(plugins_dir, plugin_id, f'{plugin_module_name}.py')
        
        if not os.path.exists(plugin_file):
            return {}
        
        spec = importlib.util.spec_from_file_location(plugin_module_name, plugin_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Get the plugin class
        plugin_class = getattr(module, plugin_class_name)
        
        # Create a mock config for the plugin
        mock_config = {'id': plugin_id}
        
        # Instantiate the plugin
        plugin_instance = plugin_class(mock_config)
        
        # Call generate_settings_template to get plugin-specific parameters
        return plugin_instance.generate_settings_template()
        
    except Exception as e:
        print(f"Failed to load plugin {plugin_id}: {e}")
        return {}

@app.route('/')
def index():
    """Main page route - renders inky.html template"""
    try:
        config = Config()
        device_config = config.config
    except Exception as e:
        print(f"Warning: Could not load device config: {e}")
        device_config = {"name": "InkyPi Display"}
    
    plugins = create_mock_plugins()
    
    return render_template('inky.html', 
                         config=device_config, 
                         plugins=plugins)

# Blueprint routes
@settings_bp.route('/settings')
def settings_page():
    """Render settings page"""
    try:
        # Create mock device settings
        mock_device_settings = {
            'name': 'InkyPi Display',
            'orientation': 'horizontal',
            'inverted_image': False,
            'timezone': 'US/Eastern',
            'time_format': '12h',
            'plugin_cycle_interval_seconds': 3600,
            'log_system_stats': True,
            'image_settings': {
                'saturation': 1.0,
                'contrast': 1.0,
                'sharpness': 1.0,
                'brightness': 1.0
            }
        }
        
        # Mock timezone list
        mock_timezones = [
            'US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific',
            'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Rome',
            'Asia/Tokyo', 'Asia/Shanghai', 'Australia/Sydney',
            'UTC'
        ]
        
        return render_template('settings.html',
                             device_settings=mock_device_settings,
                             timezones=mock_timezones)
    except Exception as e:
        return f"<h1>Error loading settings</h1><p>{str(e)}</p>", 500

@settings_bp.route('/save_settings', methods=['POST'])
def save_settings():
    """Mock save settings route"""
    return jsonify({"success": True, "message": "Settings saved successfully (mock)"})

@settings_bp.route('/download_logs/<int:hours>')
def download_logs(hours):
    """Mock download logs route"""
    from flask import make_response
    
    # Create mock log content
    log_content = f"""Mock InkyPi Logs - Last {hours} hours
    
[2025-09-02 20:00:00] INFO: InkyPi started
[2025-09-02 20:15:00] INFO: Weather plugin refreshed successfully
[2025-09-02 20:30:00] INFO: Calendar plugin refreshed successfully
[2025-09-02 20:45:00] INFO: Display updated with new content
[2025-09-02 21:00:00] INFO: Plugin cycle completed
"""
    
    response = make_response(log_content)
    response.headers["Content-Disposition"] = f"attachment; filename=inkypi_logs_{hours}h.txt"
    response.headers["Content-Type"] = "text/plain"
    return response

@settings_bp.route('/shutdown', methods=['POST'])
def shutdown():
    """Mock shutdown/reboot route"""
    import json
    from flask import request
    
    data = request.get_json()
    reboot = data.get('reboot', False)
    action = "rebooted" if reboot else "shut down"
    
    return jsonify({"success": True, "message": f"System {action} successfully (mock)"})

@plugin_bp.route('/plugin/<plugin_id>')
def plugin_page(plugin_id):
    """Render plugin settings page"""
    try:
        # Find plugin info
        plugin_path = os.path.join(plugins_dir, plugin_id)
        plugin_info_file = os.path.join(plugin_path, 'plugin-info.json')
        
        if not os.path.exists(plugin_info_file):
            return f"<h1>Plugin not found: {plugin_id}</h1>", 404
        
        # Load plugin info
        with open(plugin_info_file, 'r') as f:
            plugin_info = json.load(f)
        
        # Create mock plugin object
        plugin = type('MockPlugin', (), {
            'id': plugin_id,
            'display_name': plugin_info.get('display_name', plugin_id),
            'description': plugin_info.get('description', ''),
            'version': plugin_info.get('version', '1.0.0')
        })()
        
        # Check if settings template exists
        settings_template_path = os.path.join(plugin_path, 'settings.html')
        if os.path.exists(settings_template_path):
            settings_template = f'{plugin_id}/settings.html'
        else:
            settings_template = None
        
        # Base template parameters
        template_params = {
            'plugin': plugin,
            'settings_template': settings_template,
            'plugin_settings': {},  # Empty settings for new instance
            'plugin_instance': None,  # No existing instance
            'playlists': ['Default', 'Morning', 'Evening'],  # Mock playlists
            'api_key': None,  # Mock API key info
            'style_settings': True,  # Enable style settings
            'frame_styles': [  # Mock frame styles
                {'name': 'blank', 'icon': 'frames/blank.png'},
                {'name': 'rectangle', 'icon': 'frames/rectangle.png'},
                {'name': 'corner', 'icon': 'frames/corner.png'},
                {'name': 'top_and_bottom', 'icon': 'frames/top_and_bottom.png'}
            ],
            'ai_text_models': get_text_models(),
            'ai_image_models': get_image_models(),
            'ai_text_models_by_provider': get_text_models_by_provider(),
            'ai_image_models_by_provider': get_image_models_by_provider()
        }
        
        # Try to load plugin-specific template parameters from the actual plugin
        try:
            plugin_specific_params = load_plugin_template_params(plugin_id, plugin_info)
            template_params.update(plugin_specific_params)
        except Exception as e:
            logger.debug(f"Could not load plugin-specific params for {plugin_id}: {e}")
            # Continue without plugin-specific params
        
        return render_template('plugin.html', **template_params)
        
    except Exception as e:
        return f"<h1>Error loading plugin {plugin_id}</h1><p>{str(e)}</p>", 500

@plugin_bp.route('/plugin/<plugin_id>/image/<path:filename>')
def image(plugin_id, filename):
    """Serve plugin images - with fallback for missing icons"""
    # Plugin images can be in subdirectories (like frames/blank.png)
    plugin_path = os.path.join(plugins_dir, plugin_id)
    plugin_image_file = os.path.join(plugin_path, filename)
    
    if os.path.exists(plugin_image_file):
        # Extract directory and filename for send_from_directory
        file_dir = os.path.dirname(plugin_image_file)
        file_name = os.path.basename(plugin_image_file)
        return send_from_directory(file_dir, file_name)
    else:
        # Return a placeholder if plugin image doesn't exist
        return send_from_directory(os.path.join(static_dir, 'icons'), 'settings.png')

@plugin_bp.route('/update_now', methods=['POST'])
def update_now():
    """Mock update now route"""
    return jsonify({"success": True, "message": "Display updated (mock)"})

@plugin_bp.route('/update_plugin_instance/<instance_name>', methods=['PUT'])
def update_plugin_instance(instance_name):
    """Mock update plugin instance route"""
    return jsonify({"success": True, "message": f"Updated plugin instance {instance_name} (mock)"})

@playlist_bp.route('/add_plugin', methods=['POST'])
def add_plugin():
    """Mock add plugin to playlist route"""
    return jsonify({"success": True, "message": "Plugin added to playlist (mock)"})

@playlist_bp.route('/playlists')
def playlists():
    """Render playlists page"""
    try:
        # Create mock playlist data structure
        mock_playlists = [
            {
                'name': 'Default',
                'start_time': '00:00',
                'end_time': '24:00',
                'plugins': [
                    {
                        'name': 'Morning Weather',
                        'plugin_id': 'weather',
                        'latest_refresh_time': '2025-09-02 08:30:00'
                    },
                    {
                        'name': 'Daily Calendar',
                        'plugin_id': 'calendar', 
                        'latest_refresh_time': '2025-09-02 09:00:00'
                    }
                ]
            },
            {
                'name': 'Evening',
                'start_time': '18:00', 
                'end_time': '23:59',
                'plugins': [
                    {
                        'name': 'Evening Clock',
                        'plugin_id': 'clock',
                        'latest_refresh_time': '2025-09-02 18:00:00'
                    },
                    {
                        'name': 'Night News',
                        'plugin_id': 'newspaper',
                        'latest_refresh_time': '2025-09-02 20:30:00'
                    }
                ]
            }
        ]
        
        # Mock playlist config and refresh info
        playlist_config = type('MockPlaylistConfig', (), {
            'playlists': mock_playlists,
            'active_playlist': 'Default'
        })()
        
        refresh_info = type('MockRefreshInfo', (), {
            'playlist': 'Default',
            'plugin_instance': 'Morning Weather'
        })()
        
        return render_template('playlist.html', 
                             playlist_config=playlist_config,
                             refresh_info=refresh_info)
    except Exception as e:
        return f"<h1>Error loading playlists</h1><p>{str(e)}</p>", 500

@playlist_bp.route('/create_playlist', methods=['POST'])
def create_playlist():
    """Mock create playlist route"""
    return jsonify({"success": True, "message": "Playlist created successfully (mock)"})

@playlist_bp.route('/update_playlist/<playlist_name>', methods=['PUT']) 
def update_playlist(playlist_name):
    """Mock update playlist route"""
    return jsonify({"success": True, "message": f"Playlist '{playlist_name}' updated successfully (mock)"})

@playlist_bp.route('/delete_playlist/<playlist_name>', methods=['DELETE'])
def delete_playlist(playlist_name):
    """Mock delete playlist route"""
    return jsonify({"success": True, "message": f"Playlist '{playlist_name}' deleted successfully (mock)"})

@plugin_bp.route('/delete_plugin_instance', methods=['POST'])
def delete_plugin_instance():
    """Mock delete plugin instance route"""
    return jsonify({"success": True, "message": "Plugin instance deleted successfully (mock)"})

@plugin_bp.route('/display_plugin_instance', methods=['POST'])
def display_plugin_instance():
    """Mock display plugin instance route"""
    return jsonify({"success": True, "message": "Plugin instance displayed successfully (mock)"})

# Register blueprints
app.register_blueprint(settings_bp)
app.register_blueprint(plugin_bp)
app.register_blueprint(playlist_bp)

def create_placeholder_image(image_path):
    """Create a simple placeholder image using PIL and ensure parent directories exist"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Ensure parent directories exist
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        
        # Create a 400x300 placeholder image
        img = Image.new('RGB', (400, 300), color='lightgray')
        draw = ImageDraw.Draw(img)
        
        # Draw some text
        try:
            font = ImageFont.load_default()
        except:
            font = None
            
        text = "InkyPi Test\nPlaceholder Image"
        draw.text((50, 120), text, fill='black', font=font)
        
        # Draw a border
        draw.rectangle([(10, 10), (390, 290)], outline='black', width=2)
        
        # Save the image
        img.save(image_path)
        return True
    except ImportError:
        print("Warning: PIL not available, cannot create placeholder image")
        return False
    except Exception as e:
        print(f"Error creating placeholder image: {e}")
        return False

if __name__ == '__main__':
    print("Starting InkyPi HTML Test Server...")
    print("This simulates how inky.html would be served on the Raspberry Pi")
    print("Access the page at: http://localhost:5000")
    print("Press Ctrl+C to stop the server")
    
    # Create a placeholder current image if it doesn't exist
    current_image_path = os.path.join(static_dir, 'images', 'current_image.png')
    if not os.path.exists(current_image_path):
        print(f"Creating placeholder image at {current_image_path}")
        if create_placeholder_image(current_image_path):
            print(f"Placeholder image created successfully")
        else:
            print(f"Note: {current_image_path} not found - the page will show a broken image")
    
    app.run(debug=True, host='0.0.0.0', port=5000)