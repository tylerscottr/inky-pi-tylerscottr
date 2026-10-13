import os
import json
import uuid
import logging
from datetime import datetime, timedelta
from utils.time_utils import clock_interval_elapsed

logger = logging.getLogger(__name__)

class RefreshInfo:
    """Keeps track of refresh metadata.

    Attributes:
        refresh_time (str): ISO-formatted time string of the refresh.
        image_hash (int): SHA-256 hash of the image.
        refresh_type (str): Refresh type ['Manual Update', 'Playlist'].
        plugin_id (str): Plugin id of the refresh.
        playlist (str): Playlist name if refresh_type is 'Playlist'.
        plugin_instance (str): Plugin instance name if refresh_type is 'Playlist'.
    """

    def __init__(self, refresh_type, plugin_id, refresh_time, image_hash, playlist=None, plugin_instance=None):
        """Initialize RefreshInfo instance."""
        self.refresh_time = refresh_time
        self.image_hash = image_hash
        self.refresh_type = refresh_type
        self.plugin_id = plugin_id
        self.playlist = playlist
        self.plugin_instance = plugin_instance

    def get_refresh_datetime(self):
        """Returns the refresh time as a datetime object or None if not set."""
        latest_refresh = None
        if self.refresh_time:
            latest_refresh = datetime.fromisoformat(self.refresh_time)
        return latest_refresh

    def to_dict(self):
        refresh_dict = {
            "refresh_time": self.refresh_time,
            "image_hash": self.image_hash,
            "refresh_type": self.refresh_type,
            "plugin_id": self.plugin_id,
        }
        if self.playlist:
            refresh_dict["playlist"] = self.playlist
        if self.plugin_instance:
            refresh_dict["plugin_instance"] = self.plugin_instance
        return refresh_dict

    @classmethod
    def from_dict(cls, data):
        return cls(
            refresh_time=data.get("refresh_time"),
            image_hash=data.get("image_hash"),
            refresh_type=data.get("refresh_type"),
            plugin_id=data.get("plugin_id"),
            playlist=data.get("playlist"),
            plugin_instance=data.get("plugin_instance")
        )

class DisplayHold:
    """A user's choice that keeps something on screen instead of the playlist schedule.

    Attributes:
        kind (str): PLAYLIST keeps a playlist cycling, INSTANCE keeps one plugin instance's image, and
            MANUAL keeps an image rendered from ad hoc settings.
        started_at (str): ISO-formatted start time.
        ends_at (str): ISO-formatted time the hold ends at the latest, or None when pinned.
        min_until (str): In minimum mode, the hold ends at the first schedule change after this time.
        scheduled_playlist_id (str): Id of the scheduled playlist when the hold started.
        playlist_id (str): Held playlist, or the playlist of the held instance.
        plugin_id (str): Plugin id of the held instance or manual image.
        instance_name (str): Name of the held instance.
    """
    PLAYLIST = "playlist"
    INSTANCE = "instance"
    MANUAL = "manual"

    def __init__(self, kind, started_at, ends_at=None, min_until=None, scheduled_playlist_id=None,
                 playlist_id=None, plugin_id=None, instance_name=None):
        self.kind = kind
        self.started_at = started_at
        self.ends_at = ends_at
        self.min_until = min_until
        self.scheduled_playlist_id = scheduled_playlist_id
        self.playlist_id = playlist_id
        self.plugin_id = plugin_id
        self.instance_name = instance_name

    @property
    def pinned(self):
        return self.ends_at is None and self.min_until is None

    def get_ends_at_dt(self):
        return datetime.fromisoformat(self.ends_at) if self.ends_at else None

    def get_min_until_dt(self):
        return datetime.fromisoformat(self.min_until) if self.min_until else None

    def to_dict(self):
        return dict(vars(self))

    @classmethod
    def from_dict(cls, data):
        if not data:
            return None
        return cls(**data)

class PlaylistManager:
    """A class managing multiple time-based playlists.

    Attributes:
        playlists (list): A list of Playlist instances managed by the manager.
        active_playlist (str): Name of the currently active playlist.
    """
    DEFAULT_PLAYLIST_START = "00:00"
    DEFAULT_PLAYLIST_END = "24:00"
    DEFAULT_CYCLE_INTERVAL_SECONDS = 60 * 60

    def __init__(self, playlists=[], active_playlist=None):
        """Initialize PlaylistManager with a list of playlists."""
        self.playlists = playlists
        self.active_playlist = active_playlist

    def get_playlist_names(self):
        """Returns a list of all playlist names."""
        return [p.name for p in self.playlists]

    def add_default_playlist(self):
        """Add a default playlist to the manager, called when no playlists exist."""
        return self.playlists.append(
            Playlist("Default", PlaylistManager.DEFAULT_PLAYLIST_START, PlaylistManager.DEFAULT_PLAYLIST_END, []))

    def find_plugin(self, plugin_id, instance):
        """Searches playlists to find a plugin with the given ID and instance."""
        for playlist in self.playlists:
            plugin = playlist.find_plugin(plugin_id, instance)
            if plugin:
                return plugin
        return None

    def determine_active_playlist(self, current_datetime):
        """Determine the active playlist based on the current time."""
        current_time = current_datetime.strftime("%H:%M")  # Get current time in "HH:MM" format

        # get active playlists that have plugins
        active_playlists = [p for p in self.playlists if p.plugins and p.is_active(current_time)]
        if not active_playlists:
            return None

        # Sort playlists by priority
        active_playlists.sort(key=lambda p: p.get_priority())
        playlist = active_playlists[0]

        return playlist

    def get_playlist(self, playlist_name):
        """Returns the playlist with the specified name."""
        return next((p for p in self.playlists if p.name == playlist_name), None)

    def get_playlist_by_id(self, playlist_id):
        """Returns the playlist with the specified id."""
        return next((p for p in self.playlists if p.id == playlist_id), None)

    def add_plugin_to_playlist(self, playlist_name, plugin_data):
        """Adds a plugin to a playlist by the specified name. Returns true if successfully added,
        False if playlist doesn't exist"""
        playlist = self.get_playlist(playlist_name)
        if playlist:
            if playlist.add_plugin(plugin_data):
                return True
        else:
            logger.warning(f"Playlist '{playlist_name}' not found.")
        return False

    def add_playlist(self, name, start_time=None, end_time=None, cycle_interval_seconds=None, scheduled=True):
        """Creates and adds a new playlist with the given start and end times."""
        if not start_time:
            start_time = PlaylistManager.DEFAULT_PLAYLIST_START
        if not end_time:
            end_time = PlaylistManager.DEFAULT_PLAYLIST_END
        self.playlists.append(Playlist(name, start_time, end_time, cycle_interval_seconds=cycle_interval_seconds,
                                       scheduled=scheduled))
        return True

    def update_playlist(self, old_name, new_name, start_time, end_time, cycle_interval_seconds=None, scheduled=True):
        """Updates an existing playlist's name, start time, end time, cycle interval, and scheduling."""
        playlist = self.get_playlist(old_name)
        if playlist:
            playlist.name = new_name
            playlist.start_time = start_time
            playlist.end_time = end_time
            playlist.scheduled = scheduled
            if cycle_interval_seconds:
                playlist.cycle_interval_seconds = cycle_interval_seconds
            return True
        logger.warning(f"Playlist '{old_name}' not found.")
        return False

    def move_playlist(self, name, offset):
        """Moves a playlist up (negative offset) or down the list. Returns False if it can't move."""
        playlist = self.get_playlist(name)
        if not playlist:
            return False
        index = self.playlists.index(playlist)
        new_index = index + offset
        if new_index < 0 or new_index >= len(self.playlists):
            return False
        self.playlists.insert(new_index, self.playlists.pop(index))
        return True

    def delete_playlist(self, name):
        """Deletes the playlist with the specified name."""
        self.playlists = [p for p in self.playlists if p.name != name]

    def to_dict(self):
        return {
            "playlists": [p.to_dict() for p in self.playlists],
            "active_playlist": self.active_playlist
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            playlists=[Playlist.from_dict(p) for p in data.get("playlists", [])],
            active_playlist=data.get("active_playlist")
        )

class Playlist:
    """Represents a playlist with a time interval.

    Attributes:
        id (str): Stable id that survives renames.
        name (str): Name of the playlist.
        start_time (str): Playlist start time in 'HH:MM'.
        end_time (str): Playlist end time in 'HH:MM'.
        plugins (list): A list of PluginInstance objects within the playlist.
        current_plugin_index (int): Index of the most recently displayed plugin in the playlist.
        cycle_interval_seconds (int): How often the playlist moves to its next plugin.
        scheduled (bool): False for playlists that are only shown when chosen with a button.
    """

    def __init__(self, name, start_time, end_time, plugins=None, current_plugin_index=None, id=None,
                 cycle_interval_seconds=None, scheduled=True):
        self.id = id or uuid.uuid4().hex
        self.name = name
        self.start_time = start_time
        self.end_time = end_time
        self.plugins = [PluginInstance.from_dict(p) for p in (plugins or [])]
        self.current_plugin_index = current_plugin_index
        self.cycle_interval_seconds = cycle_interval_seconds or PlaylistManager.DEFAULT_CYCLE_INTERVAL_SECONDS
        self.scheduled = scheduled

    def is_active(self, current_time):
        """Check if the playlist is active at the given time."""
        if not self.scheduled:
            return False
        if self.start_time <= self.end_time:
            # Non-wrapping window (EG: 09:00-15:00)
            return self.start_time <= current_time < self.end_time
        else:
            # Wrapping window across midnight (EG: 21:00-03:00)
            return current_time >= self.start_time or current_time < self.end_time

    def add_plugin(self, plugin_data):
        """Add a new plugin instance to the playlist."""
        if self.find_plugin(plugin_data["plugin_id"], plugin_data["name"]):
            logger.warning(f"Plugin '{plugin_data['plugin_id']}' with instance '{plugin_data['name']}' already exists.")
            return False
        self.plugins.append(PluginInstance.from_dict(plugin_data))
        return True

    def update_plugin(self, plugin_id, instance_name, updated_data):
        """Updates an existing plugin instance in the playlist."""
        plugin = self.find_plugin(plugin_id, instance_name)
        if plugin:
            plugin.update(updated_data)
            return True
        logger.warning(f"Plugin '{plugin_id}' with name '{instance_name}' not found.")
        return False

    def delete_plugin(self, plugin_id, name):
        """Remove a specific plugin instance from the playlist."""
        plugin = self.find_plugin(plugin_id, name)
        if not plugin:
            logger.warning(f"Plugin '{plugin_id}' with instance '{name}' not found.")
            return False

        # Keep the rotation pointing at the same position, so the plugin after the deleted one comes next.
        removed_index = self.plugins.index(plugin)
        self.plugins.remove(plugin)
        if self.current_plugin_index is not None and removed_index <= self.current_plugin_index:
            self.current_plugin_index -= 1
            if self.current_plugin_index < 0 or not self.plugins:
                self.current_plugin_index = None
        return True

    def find_plugin(self, plugin_id, name):
        """Find a plugin instance by its plugin_id and name."""
        return next((p for p in self.plugins if p.plugin_id == plugin_id and p.name == name), None)

    def current_plugin(self):
        """Returns the most recently displayed plugin instance, or None."""
        if self.current_plugin_index is None or not self.plugins:
            return None
        return self.plugins[self.current_plugin_index % len(self.plugins)]

    def peek_next_plugin(self, step=1):
        """Returns the plugin instance `step` places after the current one without moving the rotation."""
        if not self.plugins:
            return None
        if self.current_plugin_index is None:
            return self.plugins[0] if step > 0 else self.plugins[-1]
        return self.plugins[(self.current_plugin_index + step) % len(self.plugins)]

    def advance_to(self, plugin_instance):
        """Moves the rotation to the given plugin instance after it has been displayed."""
        if plugin_instance in self.plugins:
            self.current_plugin_index = self.plugins.index(plugin_instance)

    def get_priority(self):
        """Determine priority of a playlist, based on the time range"""
        return self.get_time_range_minutes()

    def get_time_range_minutes(self):
        """Calculate the time difference in minutes between start_time and end_time."""
        start = datetime.strptime(self.start_time, "%H:%M")
        # Handle '24:00' by converting it to '00:00' of the next day
        if self.end_time != "24:00":
            end = datetime.strptime(self.end_time, "%H:%M")
        else:
            end = datetime.strptime("00:00", "%H:%M")
            end += timedelta(days=1)

        # If the window wraps past midnight (EG: 21:00 -> 03:00), treat end as next day
        if end < start:
            end += timedelta(days=1)

        return int((end - start).total_seconds() // 60)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "cycle_interval_seconds": self.cycle_interval_seconds,
            "scheduled": self.scheduled,
            "plugins": [p.to_dict() for p in self.plugins],
            "current_plugin_index": self.current_plugin_index
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data.get("id"),
            name=data["name"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            plugins=data["plugins"],
            current_plugin_index=data.get("current_plugin_index", None),
            cycle_interval_seconds=data.get("cycle_interval_seconds"),
            scheduled=data.get("scheduled", True)
        )

class PluginInstance:
    """Represents an individual plugin instance within a playlist.

    Attributes:
        plugin_id (str): Plugin id for this instance.
        name (str): Name of the plugin instance.
        settings (dict): Settings associated with the plugin.
        refresh (dict): Refresh settings, such as interval and scheduled time.
        latest_refresh (str): ISO-formatted string representing the last refresh time.
        last_render_seconds (float): How long the last render took.
        button_actions (dict): Long press action for each button while this instance is on screen.
    """

    def __init__(self, plugin_id, name, settings, refresh, latest_refresh_time=None, last_render_seconds=None,
                 button_actions=None):
        self.plugin_id = plugin_id
        self.name = name
        self.settings = settings
        self.refresh = refresh
        self.latest_refresh_time = latest_refresh_time
        self.last_render_seconds = last_render_seconds
        self.button_actions = button_actions or {}

    def update(self, updated_data):
        """Update attributes of the class with the dictionary values."""
        for key, value in updated_data.items():
            setattr(self, key, value)

    def should_refresh(self, current_time):
        """Checks whether the plugin should be refreshed based on its refresh settings and the current time."""
        latest_refresh_dt = self.get_latest_refresh_dt()
        if not latest_refresh_dt:
            return True

        # Interval refreshes come due on wall-clock multiples, e.g. :00 and :30 for 30 minutes.
        if "interval" in self.refresh:
            interval = self.refresh.get("interval")
            if interval and clock_interval_elapsed(latest_refresh_dt, current_time, interval):
                return True

        # Check for scheduled refresh (HH:MM format)
        if "scheduled" in self.refresh:
            scheduled_time_str = self.refresh.get("scheduled")
            latest_refresh_str = latest_refresh_dt.strftime("%H:%M")

            # If the latest refresh is before the scheduled time today
            if latest_refresh_str < scheduled_time_str:
                return True

        if "scheduled" in self.refresh:
            scheduled_time_str = self.refresh.get("scheduled")
            scheduled_time = datetime.strptime(scheduled_time_str, "%H:%M").time()

            latest_refresh_date = latest_refresh_dt.date()
            current_date = current_time.date()

            # Determine if a refresh is needed based on scheduled time and last refresh
            if (latest_refresh_date < current_date and current_time.time() >= scheduled_time) or \
            (latest_refresh_date == current_date and latest_refresh_dt.time() < scheduled_time <= current_time.time()):
                return True

        return False

    def get_image_path(self):
        """Formats the image path for this plugin instance."""
        return f"{self.plugin_id}_{self.name.replace(' ', '_')}.png"

    def get_latest_refresh_dt(self):
        """Returns the latest refresh time as a datetime object, or None if not set."""
        latest_refresh = None
        if self.latest_refresh_time:
            latest_refresh = datetime.fromisoformat(self.latest_refresh_time)
        return latest_refresh

    def to_dict(self):
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "plugin_settings": self.settings,
            "refresh": self.refresh,
            "latest_refresh_time": self.latest_refresh_time,
            "last_render_seconds": self.last_render_seconds,
            "button_actions": self.button_actions,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            plugin_id=data["plugin_id"],
            name=data["name"],
            settings=data["plugin_settings"],
            refresh=data["refresh"],
            latest_refresh_time=data.get("latest_refresh_time"),
            last_render_seconds=data.get("last_render_seconds"),
            button_actions=data.get("button_actions"),
        )
