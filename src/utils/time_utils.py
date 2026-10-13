import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

def calculate_seconds(interval, unit):
    seconds = 5 * 60 # default to five minutes
    if unit == "minute":
        seconds = interval * 60
    elif unit == "hour":
        seconds = interval * 60 * 60
    elif unit == "day":
        seconds = interval * 60 * 60 * 24
    else:
        logger.warning(f"Unrecognized unit: {unit}, defaulting to 5 minutes")
    return seconds

def _wall_clock_seconds(dt):
    """Seconds since the epoch on dt's local wall clock, so intervals line up with local midnight."""
    return dt.timestamp() + dt.utcoffset().total_seconds()

def clock_interval_elapsed(latest_dt, current_dt, interval_seconds):
    """Return True once current_dt reaches a wall-clock multiple of the interval after latest_dt.

    A 30 minute interval comes due at :00 and :30, regardless of when latest_dt fell.
    """
    if latest_dt is None:
        return True

    latest_dt = latest_dt.astimezone(current_dt.tzinfo)
    # Covers the wall clock repeating an hour when daylight saving time ends.
    if current_dt - latest_dt >= timedelta(seconds=interval_seconds):
        return True

    latest_slot = _wall_clock_seconds(latest_dt) // interval_seconds
    current_slot = _wall_clock_seconds(current_dt) // interval_seconds
    return current_slot > latest_slot

def seconds_until_next_interval(current_dt, interval_seconds):
    """Return the seconds from current_dt until the next wall-clock multiple of the interval."""
    return interval_seconds - _wall_clock_seconds(current_dt) % interval_seconds
