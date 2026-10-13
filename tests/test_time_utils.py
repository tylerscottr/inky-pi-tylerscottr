from datetime import datetime

import pytest
import pytz

from src.model import PluginInstance
from src.utils.time_utils import clock_interval_elapsed, seconds_until_next_interval

DENVER = pytz.timezone("America/Denver")
HALF_HOUR = 30 * 60


def denver(hour, minute, second=0, day=12, month=10):
    return DENVER.localize(datetime(2026, month, day, hour, minute, second))


class TestClockIntervalElapsed:

    @pytest.mark.parametrize(
        "latest,current,expected",
        [
            (denver(12, 31, 10), denver(12, 59, 59), False),  # same half hour slot
            (denver(12, 31, 10), denver(13, 0, 1), True),     # next slot starts at :00
            (denver(12, 0, 40), denver(12, 29, 0), False),
            (denver(12, 0, 40), denver(12, 30, 0), True),     # due exactly at :30
            (denver(12, 59, 50), denver(13, 0, 5), True),     # a new slot, even seconds later
        ],
    )
    def test_due_on_wall_clock_slots(self, latest, current, expected):
        assert clock_interval_elapsed(latest, current, HALF_HOUR) == expected

    def test_no_previous_refresh_is_due(self):
        assert clock_interval_elapsed(None, denver(12, 0), HALF_HOUR)

    def test_latest_saved_in_utc_is_compared_in_local_time(self):
        latest = datetime(2026, 10, 12, 18, 10, tzinfo=pytz.utc)  # 12:10 in Denver
        assert not clock_interval_elapsed(latest, denver(12, 25), HALF_HOUR)
        assert clock_interval_elapsed(latest, denver(12, 31), HALF_HOUR)

    def test_due_when_daylight_saving_time_repeats_the_hour(self):
        # On 2026-11-01 Denver's clock goes from 01:59 MDT back to 01:00 MST.
        latest = DENVER.localize(datetime(2026, 11, 1, 1, 40), is_dst=True)
        current = DENVER.localize(datetime(2026, 11, 1, 1, 20), is_dst=False)  # 40 minutes later
        assert clock_interval_elapsed(latest, current, HALF_HOUR)


class TestSecondsUntilNextInterval:

    @pytest.mark.parametrize(
        "current,interval,expected",
        [
            (denver(12, 3, 20), 5 * 60, 100),
            (denver(12, 5, 0), 5 * 60, 300),          # on a boundary, wait for the next one
            (denver(12, 10, 0), HALF_HOUR, 20 * 60),
            (denver(23, 0, 0), 2 * 60 * 60, 60 * 60), # two hour slots line up with local midnight
        ],
    )
    def test_aligned_to_local_clock(self, current, interval, expected):
        assert seconds_until_next_interval(current, interval) == expected

    def test_half_hour_offset_time_zone_aligns_to_local_hour(self):
        kolkata = pytz.timezone("Asia/Kolkata")
        current = kolkata.localize(datetime(2026, 10, 12, 12, 10))
        assert seconds_until_next_interval(current, 60 * 60) == 50 * 60


class TestModelRefreshChecks:

    def test_plugin_instance_interval_refresh_is_clock_aligned(self):
        instance = PluginInstance("unsplash", "Wildlife Slideshow", {}, {"interval": HALF_HOUR},
                                  latest_refresh_time=denver(12, 31, 10).isoformat())
        assert not instance.should_refresh(denver(12, 59))
        assert instance.should_refresh(denver(13, 0, 5))
