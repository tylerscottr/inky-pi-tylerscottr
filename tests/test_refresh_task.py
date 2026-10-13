import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import pytz
from PIL import Image

import src.refresh_task as refresh_task_module
from src.config import Config
from src.refresh_task import RefreshTask

DENVER = pytz.timezone("America/Denver")


def denver(hour, minute, second=0):
    return DENVER.localize(datetime(2026, 10, 12, hour, minute, second))


def instance(name, **extra):
    return {"plugin_id": "clock", "name": name, "plugin_settings": {"name": name, **extra},
            "refresh": {"interval": 60 * 60}}


def playlist(name, plugins, start="00:00", end="24:00", cycle=5 * 60, scheduled=True):
    return {"id": name.lower(), "name": name, "start_time": start, "end_time": end,
            "cycle_interval_seconds": cycle, "scheduled": scheduled, "plugins": plugins}


class FakePlugin:
    """Renders a distinct image on every call and records which instances it rendered."""

    def __init__(self):
        self.rendered = []

    def generate_image(self, settings, device_config):
        if settings.get("fail"):
            raise RuntimeError("render failed")
        self.rendered.append(settings["name"])
        return Image.new("RGB", (8, 8), (len(self.rendered) % 256, 0, 0))

    def run_button_action(self, action_id, settings, device_config):
        self.rendered.append(f"{settings['name']}:{action_id}")
        return Image.new("RGB", (8, 8), (0, len(self.rendered) % 256, 0))


class FakeDisplayManager:
    def __init__(self):
        self.images = []

    def display_image(self, image, image_settings=None):
        self.images.append(image)


class Harness:
    """Drives a RefreshTask step by step on a fake clock, without its thread."""

    def __init__(self, tmp_path, monkeypatch, playlists, **config):
        config_file = tmp_path / "device.json"
        config_file.write_text(json.dumps({
            "timezone": "America/Denver",
            "resolution": [8, 8],
            "playlist_config": {"playlists": playlists},
            "refresh_info": {},
            **config,
        }))
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        monkeypatch.setattr(Config, "config_file", str(config_file))
        monkeypatch.setattr(Config, "plugin_image_dir", str(image_dir))

        self.plugin = FakePlugin()
        monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda config: self.plugin)

        self.config = Config()
        self.display = FakeDisplayManager()
        self.task = RefreshTask(self.config, self.display)
        self.task._clock_synced = True
        self.now = denver(12, 0, 30)
        self.task._now = lambda: self.now
        self.task._restore_state()

    def run(self, at=None):
        """Plans at the given time, then runs everything queued."""
        if at:
            self.now = at
        while True:
            with self.task.condition:
                self.task._plan(self.now)
                work = self.task._pop_work()
            if not work:
                return
            self.task._execute(work)

    @property
    def shown(self):
        info = self.config.get_refresh_info()
        return info.plugin_instance

    def playlist(self, name):
        return self.config.get_playlist_manager().get_playlist(name)


@pytest.fixture
def two_playlists(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch, [
        playlist("Default", [instance("a"), instance("b"), instance("c")]),
        playlist("Photos", [instance("p1"), instance("p2")], scheduled=False),
    ])


class TestSchedule:

    def test_startup_displays_the_scheduled_playlist(self, two_playlists):
        h = two_playlists
        h.run()
        assert h.shown == "a"
        assert len(h.display.images) == 1

    def test_cycle_advances_on_clock_slots(self, two_playlists):
        h = two_playlists
        h.run()
        h.run(at=denver(12, 4, 59))
        assert h.shown == "a"
        h.run(at=denver(12, 5, 1))
        assert h.shown == "b"
        h.run(at=denver(12, 10, 1))
        assert h.shown == "c"
        assert h.playlist("Default").current_plugin().name == "c"

    def test_saved_image_is_reused_until_the_instance_is_due(self, two_playlists):
        h = two_playlists
        for minute in [0, 5, 10, 15]:
            h.run(at=denver(12, minute, 1))
        assert h.shown == "a"
        assert h.plugin.rendered == ["a", "b", "c"]  # "a" was rendered under an hour ago

    def test_window_start_switches_the_screen_right_away(self, tmp_path, monkeypatch):
        h = Harness(tmp_path, monkeypatch, [
            playlist("Default", [instance("a")]),
            playlist("Morning", [instance("m")], start="13:00", end="14:00", cycle=60 * 60),
        ])
        h.run()
        assert h.shown == "a"
        h.run(at=denver(13, 0, 1))
        assert h.shown == "m"
        h.run(at=denver(14, 0, 1))
        assert h.shown == "a"

    def test_single_plugin_playlist_only_redraws_when_its_plugin_is_due(self, tmp_path, monkeypatch):
        h = Harness(tmp_path, monkeypatch, [playlist("Default", [instance("a")])])
        h.run()
        work = []
        h.task._execute = lambda w: work.append(w[0])
        for minute in [5, 10, 30, 55]:
            h.run(at=denver(12, minute, 1))
        assert work == []  # the cycle doesn't touch a playlist with nothing to rotate to
        del h.task._execute
        h.run(at=denver(13, 0, 1))  # "a" refreshes hourly
        assert h.plugin.rendered == ["a", "a"]

    def test_single_plugin_playlist_retries_a_failed_render_after_a_cycle(self, tmp_path, monkeypatch):
        h = Harness(tmp_path, monkeypatch, [playlist("Default", [instance("a", fail=True)])])
        attempts = []
        original = h.task._run_display
        h.task._run_display = lambda request: (attempts.append(h.now), original(request))[1]
        h.run()
        h.run(at=denver(12, 3))
        assert len(attempts) == 1
        h.run(at=denver(12, 5, 31))
        assert len(attempts) == 2

    def test_failing_plugin_is_skipped(self, tmp_path, monkeypatch):
        h = Harness(tmp_path, monkeypatch, [playlist("Default", [instance("a", fail=True), instance("b")])])
        h.run()
        assert h.display.images == []
        h.run(at=denver(12, 5, 1))
        assert h.shown == "b"

    def test_restart_does_not_redraw_within_the_same_slot(self, two_playlists, tmp_path, monkeypatch):
        two_playlists.run()
        restarted = Harness.__new__(Harness)
        restarted.__dict__.update(two_playlists.__dict__)
        restarted.config = Config()
        restarted.display = FakeDisplayManager()
        restarted.task = RefreshTask(restarted.config, restarted.display)
        restarted.task._clock_synced = True
        restarted.task._now = lambda: restarted.now
        restarted.task._restore_state()
        restarted.run(at=denver(12, 3))
        assert restarted.display.images == []


class TestQueue:

    def test_display_requests_merge(self, two_playlists):
        task = two_playlists.task
        with task.condition:
            first = task._queue_display()
            second = task._queue_display(force=True)
        work = task._pop_work()
        assert work[0] == "display" and work[1].force and work[2] == [first, second]
        assert task._pop_work() is None

    def test_user_jobs_run_before_displays_and_prerenders(self, two_playlists):
        task = two_playlists.task
        with task.condition:
            task._prerenders["photos"] = True
            task._queue_display()
            task._queue_job(lambda: None)
        assert [task._pop_work()[0] for _ in range(3)] == ["job", "display", "prerender"]

    def test_prerender_renders_without_displaying(self, two_playlists):
        h = two_playlists
        h.config.update_value("prerender_mode", "always")
        h.run()
        h.run(at=denver(12, 5, 1))
        assert "p1" in h.plugin.rendered
        assert h.shown == "b"
        assert h.playlist("Photos").current_plugin() is None


class TestHoldsAndButtons:

    def test_next_playlist_holds_it_until_the_hold_ends(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.handle_button("B")
        h.run()
        assert h.shown == "p1"
        h.run(at=denver(12, 5, 1))
        assert h.shown == "p2"  # the held playlist keeps cycling
        h.run(at=denver(12, 31))
        assert h.shown == "b"   # back to the schedule after 30 minutes

    def test_refresh_rerenders_and_holds_the_instance(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.handle_button("C")
        h.run()
        assert h.plugin.rendered == ["a", "a"]
        h.run(at=denver(12, 5, 1))
        assert h.shown == "a"

    def test_presses_are_ignored_until_the_previous_one_is_displayed(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.handle_button("B")
        assert h.task.handle_button("B") is None
        assert h.task.handle_button("C", long_press=True) is None
        h.run()
        assert h.shown == "p1"
        assert h.task.handle_button("A") is not None
        h.run()
        assert h.shown == "b"

    def test_presses_are_accepted_again_if_an_action_never_finishes(self, two_playlists):
        h = two_playlists
        h.task.handle_button("B")
        h.task._button_pressed_at -= refresh_task_module.BUTTON_BUSY_TIMEOUT_SECONDS
        assert h.task.handle_button("C") is not None

    def test_press_before_anything_is_displayed_uses_the_upcoming_plugin(self, two_playlists):
        h = two_playlists
        h.task.handle_button("C")
        h.run()
        assert h.shown == "a"
        assert h.config.display_hold.instance_name == "a"

    def test_previous_plugin_wraps_around(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.perform_action("previous_plugin")
        h.run()
        assert h.shown == "c"

    def test_repeated_presses_render_once(self, two_playlists):
        h = two_playlists
        h.run()
        for _ in range(2):
            h.task.perform_action("next_plugin")
        h.run()
        assert h.shown == "c"
        assert h.plugin.rendered == ["a", "c"]

    def test_screen_off_keeps_rendering_and_any_press_wakes_it(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.handle_button("D")
        h.run()
        assert h.config.screen_off
        displayed = len(h.display.images)
        h.run(at=denver(12, 5, 1))
        assert h.plugin.rendered == ["a", "b"]
        assert len(h.display.images) == displayed
        h.task.handle_button("B")  # wakes without switching playlists
        h.run()
        assert not h.config.screen_off
        assert h.shown == "a"
        assert len(h.display.images) == displayed + 1

    def test_long_press_falls_back_to_the_instance_action(self, two_playlists):
        h = two_playlists
        h.run()
        h.playlist("Default").plugins[0].button_actions = {"A": "plugin:previous_verse"}
        h.task.handle_button("A", long_press=True)
        h.run()
        assert h.plugin.rendered[-1] == "a:previous_verse"

    def test_global_long_press_wins(self, two_playlists):
        h = two_playlists
        h.config.update_value("buttons", {"A": {"long": "next_playlist"}})
        h.run()
        h.playlist("Default").plugins[0].button_actions = {"A": "plugin:previous_verse"}
        h.task.handle_button("A", long_press=True)
        h.run()
        assert h.shown == "p1"

    def test_pin_holds_until_pressed_again(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.perform_action("pin")
        h.run(at=denver(18, 0))
        assert h.shown == "a"
        h.task.perform_action("pin")
        h.run()
        assert h.shown == "b"

    def test_web_display_waits_for_the_render_and_reports_errors(self, tmp_path, monkeypatch):
        h = Harness(tmp_path, monkeypatch, [playlist("Default", [instance("a"), instance("x", fail=True)])])
        h.run()
        default = h.playlist("Default")
        future = h.task.show_instance(default, default.plugins[1])
        h.run()
        with pytest.raises(RuntimeError):
            future.result(timeout=0)

    def test_led_shows_busy_errors_and_long_presses(self, tmp_path, monkeypatch):
        h = Harness(tmp_path, monkeypatch, [playlist("Default", [instance("a"), instance("x", fail=True)])])
        led = Mock()
        h.task.led = led
        h.run()
        led.set_starting.assert_called_with(False)
        assert [c.args[0] for c in led.set_busy.call_args_list][-1] is False

        h.task.handle_button("C", long_press=True)  # long press defaults to no action
        led.flash.assert_called_once()
        led.clear_error.assert_called()

        h.task.perform_action("next_plugin")
        assert led.set_busy.call_args.args[0] is True  # busy from the moment it's queued
        h.run()
        led.error.assert_called_once()
        assert led.set_busy.call_args.args[0] is False

    def test_deleted_held_instance_ends_the_hold(self, two_playlists):
        h = two_playlists
        h.run()
        h.task.perform_action("next_plugin")
        h.run()
        h.playlist("Default").delete_plugin("clock", "b")
        h.run()
        assert h.config.display_hold is None
