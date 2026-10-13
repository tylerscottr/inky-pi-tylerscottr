import json
from datetime import datetime

import pytest

from src.config import Config
from src.model import DisplayHold, Playlist, PlaylistManager


def plugin(name):
    return {"plugin_id": "clock", "name": name, "plugin_settings": {}, "refresh": {"interval": 3600}}


def playlist_with(*names, start="00:00", end="24:00"):
    return Playlist("Test Playlist", start, end, plugins=[plugin(n) for n in names])


class TestRotation:

    def test_peek_does_not_move_the_rotation(self):
        playlist = playlist_with("a", "b", "c")
        assert playlist.peek_next_plugin().name == "a"
        assert playlist.peek_next_plugin().name == "a"
        assert playlist.current_plugin() is None

    def test_advance_to_moves_the_rotation(self):
        playlist = playlist_with("a", "b", "c")
        playlist.advance_to(playlist.plugins[2])
        assert playlist.current_plugin().name == "c"
        assert playlist.peek_next_plugin().name == "a"
        assert playlist.peek_next_plugin(-1).name == "b"

    @pytest.mark.parametrize(
        "current,deleted,expected_next",
        [
            (1, "b", "c"),  # the shown plugin is deleted, the one after it comes next
            (1, "a", "c"),  # a plugin before the shown one is deleted
            (1, "c", "a"),  # a plugin after the shown one is deleted
            (0, "a", "b"),
        ],
    )
    def test_delete_keeps_the_rotation_position(self, current, deleted, expected_next):
        playlist = playlist_with("a", "b", "c")
        playlist.current_plugin_index = current
        playlist.delete_plugin("clock", deleted)
        assert playlist.peek_next_plugin().name == expected_next

    def test_delete_last_plugin_resets_the_rotation(self):
        playlist = playlist_with("a")
        playlist.current_plugin_index = 0
        playlist.delete_plugin("clock", "a")
        assert playlist.current_plugin_index is None


class TestPlaylistManager:

    def test_move_playlist(self):
        manager = PlaylistManager([Playlist(n, "00:00", "24:00") for n in ["a", "b", "c"]])
        assert manager.move_playlist("c", -1)
        assert manager.get_playlist_names() == ["a", "c", "b"]
        assert not manager.move_playlist("a", -1)
        assert not manager.move_playlist("missing", 1)

    def test_buttons_only_playlist_is_never_active(self):
        photos = Playlist("Photos", "00:00", "24:00", plugins=[plugin("p")], scheduled=False)
        default = playlist_with("a")
        manager = PlaylistManager([photos, default])
        assert not photos.is_active("12:00")
        assert manager.determine_active_playlist(datetime(2026, 10, 12, 12, 0)) is default
        assert Playlist.from_dict(photos.to_dict()).scheduled is False
        assert Playlist.from_dict({"name": "Old", "start_time": "00:00", "end_time": "24:00", "plugins": []}).scheduled

    def test_active_playlist_skips_empty_playlists(self):
        empty_morning = Playlist("Morning", "06:00", "09:00")
        default = playlist_with("a")
        manager = PlaylistManager([empty_morning, default])
        assert manager.determine_active_playlist(datetime(2026, 10, 12, 7, 0)) is default

    def test_display_hold_round_trip(self):
        hold = DisplayHold(DisplayHold.INSTANCE, "2026-10-12T12:00:00-06:00", ends_at="2026-10-12T12:30:00-06:00",
                           playlist_id="p", plugin_id="clock", instance_name="a")
        restored = DisplayHold.from_dict(json.loads(json.dumps(hold.to_dict())))
        assert vars(restored) == vars(hold)
        assert not restored.pinned


class TestConfigMigration:

    def test_global_cycle_interval_moves_to_each_playlist(self, tmp_path, monkeypatch):
        config_file = tmp_path / "device.json"
        config_file.write_text(json.dumps({
            "plugin_cycle_interval_seconds": 300,
            "scheduler_sleep_time": 60,
            "playlist_config": {"playlists": [
                {"name": "Default", "start_time": "00:00", "end_time": "24:00", "plugins": []},
            ]},
        }))
        monkeypatch.setattr(Config, "config_file", str(config_file))

        Config()

        saved = json.loads(config_file.read_text())
        playlist = saved["playlist_config"]["playlists"][0]
        assert playlist["cycle_interval_seconds"] == 300
        assert playlist["id"]
        assert "plugin_cycle_interval_seconds" not in saved
        assert "scheduler_sleep_time" not in saved
        assert Config().get_playlist_manager().playlists[0].id == playlist["id"]


class TestPlaylist:

    @pytest.mark.parametrize(
        "start,end,current,expected,priority",
        [
            # --- Non-wrapping cases 09:00 <-> 15:00 ---
            ("09:00", "15:00", "08:59", False, 360),  # just before start
            ("09:00", "15:00", "09:00", True, 360),   # exactly at start
            ("09:00", "15:00", "12:00", True, 360),   # during
            ("09:00", "15:00", "14:59", True, 360),   # just before end
            ("09:00", "15:00", "15:00", False, 360),  # exactly at end
            ("09:00", "15:00", "23:00", False, 360),  # way after
    
            # --- Wrapping cases (crossing midnight) 21:00 <-> 03:00 ---
            ("21:00", "03:00", "20:59", False, 360),  # just before start
            ("21:00", "03:00", "21:00", True, 360),   # exactly at start
            ("21:00", "03:00", "23:59", True, 360),   # before midnight
            ("21:00", "03:00", "00:00", True, 360),   # after midnight, inside
            ("21:00", "03:00", "02:59", True, 360),   # just before end
            ("21:00", "03:00", "03:00", False, 360),  # exactly at end
            ("21:00", "03:00", "11:00", False, 360),  # way after
    
            # --- Equal start and end 12:00 <-> 12:00 ---
            ("12:00", "12:00", "11:59", False, 0),
            ("12:00", "12:00", "12:00", False, 0),
            ("12:00", "12:00", "12:01", False, 0),
    
            # --- Midnight boundaries 18:00 <-> 00:00 ---
            ("18:00", "00:00", "17:59", False, 360),  # before start
            ("18:00", "00:00", "23:59", True, 360),   # before end
            ("18:00", "00:00", "00:00", False, 360),  # exactly at end
    
            # --- Midnight boundaries 00:00 <-> 06:00 ---
            ("00:00", "06:00", "00:00", True, 360),   # start at midnight
            ("00:00", "06:00", "05:59", True, 360),   # before end
            ("00:00", "06:00", "06:00", False, 360),  # exactly at end

            # --- All day 00:00 <-> 24:00 ---
            ("00:00", "24:00", "00:00", True, 1440),   # exactly at start
            ("00:00", "24:00", "10:00", True, 1440),   # during
            ("00:00", "24:00", "24:00", False, 1440),  # exactly at end
        ]
    )
    def test_is_active_and_priority(self, start, end, current, expected, priority):
        playlist = Playlist("Test Playlist", start, end)
        assert playlist.is_active(current) == expected
        assert playlist.get_priority() == priority
        