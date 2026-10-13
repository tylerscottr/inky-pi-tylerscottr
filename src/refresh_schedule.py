"""Time calculations for the refresh scheduler. Every function takes the current time explicitly."""
from datetime import datetime, time, timedelta

from model import DisplayHold
from utils.time_utils import seconds_until_next_interval

HOLD_FIXED = "fixed"
HOLD_MINIMUM = "minimum"
DEFAULT_HOLD_SETTINGS = {
    "mode": HOLD_FIXED,
    "fixed_minutes": 30,
    "minimum_minutes": 10,
    "maximum_minutes": 120,
}

PRERENDER_ON_SCREEN = "on_screen"
PRERENDER_JUST_IN_TIME = "just_in_time"
PRERENDER_ALWAYS = "always"
PRERENDER_MODES = [PRERENDER_ON_SCREEN, PRERENDER_JUST_IN_TIME, PRERENDER_ALWAYS]

# Lead time for a plugin that has never rendered, and the margin added to measured render times.
DEFAULT_PRERENDER_LEAD_SECONDS = 120
PRERENDER_MARGIN_SECONDS = 30


def _localize(naive, tz):
    """Attaches tz to a naive local time, using pytz's localize when available."""
    if hasattr(tz, "localize"):
        return tz.normalize(tz.localize(naive))
    return naive.replace(tzinfo=tz)


def next_occurrence(hhmm, now):
    """Returns the first time after now that the local clock reads hhmm ('24:00' is the next midnight)."""
    hours, minutes = (int(part) for part in hhmm.split(":"))
    for day_offset in range(3):
        day = now.date() + timedelta(days=day_offset)
        occurrence = _localize(datetime.combine(day, time()) + timedelta(hours=hours, minutes=minutes), now.tzinfo)
        if occurrence > now:
            return occurrence
    return None


def next_window_boundary(playlists, now):
    """Returns the next time any scheduled playlist's window starts or ends, or None if there are none."""
    boundaries = [next_occurrence(t, now) for p in playlists if p.scheduled for t in (p.start_time, p.end_time)]
    boundaries = [b for b in boundaries if b]
    return min(boundaries) if boundaries else None


def next_plugin_refresh(plugin_instance, now):
    """Returns when the plugin instance's own refresh setting next comes due, or None."""
    interval = plugin_instance.refresh.get("interval")
    if interval:
        return now + timedelta(seconds=seconds_until_next_interval(now, interval))
    scheduled = plugin_instance.refresh.get("scheduled")
    if scheduled:
        return next_occurrence(scheduled, now)
    return None


def new_hold(kind, hold_settings, now, scheduled_playlist_id, pinned=False, **target):
    """Creates a hold that starts now, ending according to the hold settings."""
    settings = {**DEFAULT_HOLD_SETTINGS, **(hold_settings or {})}
    ends_at = min_until = None
    if not pinned:
        if settings["mode"] == HOLD_MINIMUM:
            min_until = (now + timedelta(minutes=settings["minimum_minutes"])).isoformat()
            ends_at = (now + timedelta(minutes=settings["maximum_minutes"])).isoformat()
        else:
            ends_at = (now + timedelta(minutes=settings["fixed_minutes"])).isoformat()
    return DisplayHold(kind, now.isoformat(), ends_at=ends_at, min_until=min_until,
                       scheduled_playlist_id=scheduled_playlist_id, **target)


def hold_expired(hold, now, scheduled_playlist_id):
    """Whether the hold has ended.

    A hold ends at its end time. In minimum mode it also ends once the minimum has passed and the scheduled
    playlist differs from the one scheduled when the hold started.
    """
    ends_at = hold.get_ends_at_dt()
    if ends_at and now >= ends_at:
        return True
    min_until = hold.get_min_until_dt()
    return bool(min_until and now >= min_until and scheduled_playlist_id != hold.scheduled_playlist_id)


def hold_wake_times(hold):
    """Times at which the hold could end."""
    return [t for t in (hold.get_ends_at_dt(), hold.get_min_until_dt()) if t]


def prerender_lead(plugin_instance):
    """How long before it's shown a plugin instance should start rendering."""
    if plugin_instance is None or not plugin_instance.last_render_seconds:
        return timedelta(seconds=DEFAULT_PRERENDER_LEAD_SECONDS)
    return timedelta(seconds=plugin_instance.last_render_seconds * 1.5 + PRERENDER_MARGIN_SECONDS)
