from datetime import datetime, timedelta

import pytest
import pytz

from src.model import DisplayHold, Playlist, PluginInstance
from src.refresh_schedule import (HOLD_MINIMUM, hold_expired, new_hold, next_occurrence, next_plugin_refresh,
                                  next_window_boundary, prerender_lead)

DENVER = pytz.timezone("America/Denver")


def denver(hour, minute, day=12, month=10):
    return DENVER.localize(datetime(2026, month, day, hour, minute))


class TestWindowBoundaries:

    @pytest.mark.parametrize(
        "hhmm,now,expected",
        [
            ("09:00", denver(8, 0), denver(9, 0)),
            ("09:00", denver(9, 0), denver(9, 0, day=13)),   # already reached, so tomorrow
            ("24:00", denver(23, 30), denver(0, 0, day=13)),
            ("00:00", denver(23, 30), denver(0, 0, day=13)),
        ],
    )
    def test_next_occurrence(self, hhmm, now, expected):
        assert next_occurrence(hhmm, now) == expected

    def test_next_occurrence_across_daylight_saving_change(self):
        # 2026-11-01 has 25 hours in Denver, so 08:00 that day is 25 hours after 08:00 the day before.
        now = denver(8, 0, day=31)
        assert next_occurrence("08:00", now) - now == timedelta(hours=25)

    def test_next_window_boundary_is_the_earliest_start_or_end(self):
        playlists = [Playlist("Default", "00:00", "24:00"), Playlist("Morning", "06:00", "09:00"),
                     Playlist("Night", "21:00", "03:00")]
        assert next_window_boundary(playlists, denver(7, 0)) == denver(9, 0)
        assert next_window_boundary(playlists, denver(22, 0)) == denver(0, 0, day=13)
        assert next_window_boundary(playlists, denver(1, 0)) == denver(3, 0)
        assert next_window_boundary([], denver(1, 0)) is None

    def test_buttons_only_playlists_have_no_boundaries(self):
        playlists = [Playlist("Default", "00:00", "24:00"), Playlist("Photos", "06:00", "09:00", scheduled=False)]
        assert next_window_boundary(playlists, denver(7, 0)) == denver(0, 0, day=13)


class TestNextPluginRefresh:

    def test_interval_refresh_is_on_the_next_clock_slot(self):
        instance = PluginInstance("clock", "a", {}, {"interval": 30 * 60})
        assert next_plugin_refresh(instance, denver(12, 10)) == denver(12, 30)

    def test_daily_refresh_is_at_its_time(self):
        instance = PluginInstance("clock", "a", {}, {"scheduled": "06:00"})
        assert next_plugin_refresh(instance, denver(12, 10)) == denver(6, 0, day=13)


class TestHolds:

    def test_fixed_hold_ends_after_its_duration(self):
        hold = new_hold(DisplayHold.PLAYLIST, {"fixed_minutes": 30}, denver(12, 0), "default", playlist_id="photos")
        assert not hold_expired(hold, denver(12, 29), "default")
        assert not hold_expired(hold, denver(12, 29), "morning")  # schedule changes don't matter
        assert hold_expired(hold, denver(12, 30), "default")

    def test_minimum_hold_ends_at_the_first_schedule_change_after_the_minimum(self):
        settings = {"mode": HOLD_MINIMUM, "minimum_minutes": 10, "maximum_minutes": 120}
        hold = new_hold(DisplayHold.PLAYLIST, settings, denver(8, 55), "morning", playlist_id="photos")
        assert not hold_expired(hold, denver(9, 1), "default")   # schedule changed, but within the minimum
        assert hold_expired(hold, denver(9, 5), "default")       # minimum reached after the change
        assert not hold_expired(hold, denver(10, 0), "morning")  # no change yet
        assert hold_expired(hold, denver(10, 55), "morning")     # the maximum

    def test_pinned_hold_never_expires(self):
        hold = new_hold(DisplayHold.INSTANCE, None, denver(12, 0), "default", pinned=True, playlist_id="photos")
        assert hold.pinned
        assert not hold_expired(hold, denver(23, 0, day=20), "night")


class TestPrerenderLead:

    def test_uses_the_last_render_time_with_a_margin(self):
        instance = PluginInstance("clock", "a", {}, {}, last_render_seconds=20)
        assert prerender_lead(instance) == timedelta(seconds=60)

    def test_defaults_when_never_rendered(self):
        assert prerender_lead(PluginInstance("clock", "a", {}, {})) == timedelta(seconds=120)
        assert prerender_lead(None) == timedelta(seconds=120)
