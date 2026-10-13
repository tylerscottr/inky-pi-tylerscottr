#!/usr/bin/env python3

# set up logging
import os, logging.config

from pi_heif import register_heif_opener

logging.config.fileConfig(os.path.join(os.path.dirname(__file__), 'config', 'logging.conf'))

# suppress warning from inky library https://github.com/pimoroni/inky/issues/205
import warnings
warnings.filterwarnings("ignore", message=".*Busy Wait: Held high.*")

import os
import random
import time
import sys
import json
import logging
import threading
import argparse
from utils.app_utils import generate_startup_image
from flask import Flask, request, send_from_directory
from werkzeug.serving import is_running_from_reloader
from config import Config
from display.display_manager import DisplayManager
from refresh_task import RefreshTask
from buttons import ButtonService, default_button_config
from led import LedController, default_led_config
from blueprints.main import main_bp
from blueprints.settings import settings_bp
from blueprints.plugin import plugin_bp
from blueprints.playlist import playlist_bp
from blueprints.apikeys import apikeys_bp
from jinja2 import ChoiceLoader, FileSystemLoader
from plugins.plugin_registry import load_plugins
from utils.ai_models import model_picker
from waitress import serve


logger = logging.getLogger(__name__)

# Parse command line arguments
parser = argparse.ArgumentParser(description='InkyPi Display Server')
parser.add_argument('--dev', action='store_true', help='Run in development mode')
args = parser.parse_args()

# Set development mode settings
if args.dev:
    Config.config_file = os.path.join(Config.BASE_DIR, "config", "device_dev.json")
    DEV_MODE = True
    PORT = 8080
    logger.info("Starting InkyPi in DEVELOPMENT mode on port 8080")
else:
    DEV_MODE = False
    PORT = 80
    logger.info("Starting InkyPi in PRODUCTION mode on port 80")
logging.getLogger('waitress.queue').setLevel(logging.ERROR)
app = Flask(__name__)
template_dirs = [
   os.path.join(os.path.dirname(__file__), "templates"),    # Default template folder
   os.path.join(os.path.dirname(__file__), "plugins"),      # Plugin templates
]
app.jinja_loader = ChoiceLoader([FileSystemLoader(directory) for directory in template_dirs])

device_config = Config()
display_manager = DisplayManager(device_config)
led = LedController(device_config)
refresh_task = RefreshTask(device_config, display_manager, led)
button_service = ButtonService(device_config, refresh_task.handle_button)
app.jinja_env.globals["ai_model_picker"] = lambda *args, **kwargs: model_picker(device_config, *args, **kwargs)

# Start with the display's built-in buttons and LED until the user saves their own settings.
if device_config.get_config("buttons", default=None) is None:
    device_config.update_value("buttons", default_button_config(display_manager.default_button_pins()), write=True)
if device_config.get_config("led", default=None) is None:
    device_config.update_value("led", default_led_config(display_manager.default_led_pin()), write=True)

load_plugins(device_config.get_plugins())

# Store dependencies
app.config['DEVICE_CONFIG'] = device_config
app.config['DISPLAY_MANAGER'] = display_manager
app.config['REFRESH_TASK'] = refresh_task
app.config['BUTTON_SERVICE'] = button_service
app.config['LED'] = led
app.config['DEV_MODE'] = DEV_MODE

# Set additional parameters
app.config['MAX_FORM_PARTS'] = 10_000

# Register Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(plugin_bp)
app.register_blueprint(playlist_bp)
app.register_blueprint(apikeys_bp)

# Register opener for HEIF/HEIC images
register_heif_opener()

if __name__ == '__main__':

    # display default inkypi image on startup, queued ahead of any scheduled refresh
    if device_config.get_config("startup") is True:
        logger.info("Startup flag is set, displaying startup image")
        img = generate_startup_image(device_config.get_resolution())
        refresh_task.show_image(img)
        device_config.update_value("startup", False, write=True)

    # start the LED, background refresh task, and buttons
    led.start()
    refresh_task.start()
    button_service.start()

    try:
        # Run the Flask app
        app.secret_key = str(random.randint(100000,999999))

        # Get local IP address for display (only in dev mode when running on non-Pi)
        if DEV_MODE:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                logger.info(f"Serving on http://{local_ip}:{PORT}")
            except:
                pass  # Ignore if we can't get the IP

        # Several threads, so a page can load while another request waits for a render.
        serve(app, host="0.0.0.0", port=PORT, threads=4)
    finally:
        button_service.stop()
        refresh_task.stop()
        led.stop()
