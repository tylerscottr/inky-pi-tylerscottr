import os
import json
import logging
import threading
import time
from dotenv import load_dotenv
from model import PlaylistManager, RefreshInfo, DisplayHold

logger = logging.getLogger(__name__)

class Config:
    # Base path for the project directory
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    # File paths relative to the script's directory
    config_file = os.path.join(BASE_DIR, "config", "device.json")

    # File path for storing the current image being displayed
    current_image_file = os.path.join(BASE_DIR, "static", "images", "current_image.png")

    # Directory path for storing plugin instance images
    plugin_image_dir = os.path.join(BASE_DIR, "static", "images", "plugins")

    def __init__(self):
        # Guards the config and model objects, which the web server and the refresh task share.
        self.lock = threading.RLock()
        self.needs_migration_write = False
        self.config = self.read_config()
        self.plugins_list = self.read_plugins_list()
        self.playlist_manager = self.load_playlist_manager()
        self.refresh_info = self.load_refresh_info()
        self.display_hold = DisplayHold.from_dict(self.get_config("display_hold", default=None))
        self.screen_off = bool(self.get_config("screen_off", default=False))
        if self.needs_migration_write:
            # Save the generated playlist ids so holds and button settings can refer to them.
            self.write_config()

    def read_config(self):
        """Reads the device config JSON file and returns it as a dictionary."""
        logger.debug(f"Reading device config from {self.config_file}")
        with open(self.config_file) as f:
            config = json.load(f)

        logger.debug("Loaded config:\n%s", json.dumps(config, indent=3))

        return config

    def read_plugins_list(self):
        """Reads the plugin-info.json config JSON from each plugin folder. Excludes the base plugin."""
        # Iterate over all plugin folders
        plugins_list = []
        for plugin in sorted(os.listdir(os.path.join(self.BASE_DIR, "plugins"))):
            plugin_path = os.path.join(self.BASE_DIR, "plugins", plugin)
            if os.path.isdir(plugin_path) and plugin != "__pycache__":
                # Check if the plugin-info.json file exists
                plugin_info_file = os.path.join(plugin_path, "plugin-info.json")
                if os.path.isfile(plugin_info_file):
                    logger.debug(f"Reading plugin info from {plugin_info_file}")
                    with open(plugin_info_file) as f:
                        plugin_info = json.load(f)
                    plugins_list.append(plugin_info)

        return plugins_list

    def write_config(self):
        """Updates the cached config from the model objects and writes to the config file."""
        logger.debug(f"Writing device config to {self.config_file}")
        with self.lock:
            self.update_value("playlist_config", self.playlist_manager.to_dict())
            self.update_value("refresh_info", self.refresh_info.to_dict())
            self.update_value("display_hold", self.display_hold.to_dict() if self.display_hold else None)
            self.update_value("screen_off", self.screen_off)

            # Replace the file in one step so a power loss mid-write can't leave it truncated.
            temp_file = self.config_file + ".tmp"
            with open(temp_file, 'w') as outfile:
                json.dump(self.config, outfile, indent=4)
                outfile.flush()
                os.fsync(outfile.fileno())
            for attempt in range(5):
                try:
                    os.replace(temp_file, self.config_file)
                    break
                except PermissionError:
                    # Windows briefly locks a file that was just written, e.g. while antivirus scans it.
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))

    def get_config(self, key=None, default={}):
        """Gets the value of a specific configuration key or returns the entire config if none provided."""
        if key is not None:
            return self.config.get(key, default)
        return self.config

    def get_plugins(self):
        """Returns the list of plugin configurations, sorted by custom order if set."""
        plugin_order = self.config.get('plugin_order', [])

        if not plugin_order:
            return self.plugins_list

        # Create a dict for quick lookup
        plugins_dict = {p['id']: p for p in self.plugins_list}

        # Build ordered list
        ordered = []
        for plugin_id in plugin_order:
            if plugin_id in plugins_dict:
                ordered.append(plugins_dict.pop(plugin_id))

        # Append any remaining plugins not in the order (new plugins)
        ordered.extend(plugins_dict.values())

        return ordered

    def set_plugin_order(self, order):
        """Sets the custom plugin display order."""
        self.update_value('plugin_order', order, write=True)

    def get_plugin(self, plugin_id):
        """Finds and returns a plugin config by its ID."""
        return next((plugin for plugin in self.plugins_list if plugin['id'] == plugin_id), None)

    def get_resolution(self):
        """Returns the display resolution as a tuple (width, height) from the configuration."""
        resolution = self.get_config("resolution")
        width, height = resolution
        return (int(width), int(height))

    def update_config(self, config):
        """Updates the config with the new values provided and writes to the config file."""
        with self.lock:
            self.config.update(config)
            self.write_config()

    def update_value(self, key, value, write=False):
        """Updates a specific key in the configuration with a new value and optionally writes it to the config file."""
        with self.lock:
            self.config[key] = value
            if write:
                self.write_config()

    def load_env_key(self, key):
        """Loads an environment variable using dotenv and returns its value."""
        load_dotenv(override=True)
        return os.getenv(key)

    def load_playlist_manager(self):
        """Loads the playlist manager object from the config."""
        playlist_config = self.get_config("playlist_config")

        # Older configs had one cycle interval for every playlist.
        global_cycle_interval = self.config.pop("plugin_cycle_interval_seconds", None)
        self.config.pop("scheduler_sleep_time", None)
        for playlist in playlist_config.get("playlists", []):
            if not playlist.get("cycle_interval_seconds") or not playlist.get("id"):
                if not playlist.get("cycle_interval_seconds"):
                    playlist["cycle_interval_seconds"] = global_cycle_interval
                self.needs_migration_write = True

        playlist_manager = PlaylistManager.from_dict(playlist_config)
        if not playlist_manager.playlists:
            playlist_manager.add_default_playlist()
            self.needs_migration_write = True
        return playlist_manager

    def load_refresh_info(self):
        """Loads the refresh information from the config."""
        return RefreshInfo.from_dict(self.get_config("refresh_info"))

    def get_playlist_manager(self):
        """Returns the playlist manager."""
        return self.playlist_manager

    def get_refresh_info(self):
        """Returns the refresh information."""
        return self.refresh_info
