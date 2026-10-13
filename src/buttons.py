"""Hardware buttons wired to GPIO pins, read through libgpiod."""
import logging
import threading
import time
from datetime import timedelta

logger = logging.getLogger(__name__)

BUTTONS = ["A", "B", "C", "D"]

ACTION_NONE = "none"
ACTION_REFRESH = "refresh"
ACTION_NEXT_PLUGIN = "next_plugin"
ACTION_PREVIOUS_PLUGIN = "previous_plugin"
ACTION_NEXT_PLAYLIST = "next_playlist"
ACTION_PREVIOUS_PLAYLIST = "previous_playlist"
ACTION_PIN = "pin"
ACTION_SCREEN_OFF = "screen_off"
PLAYLIST_ACTION_PREFIX = "playlist:"
PLUGIN_ACTION_PREFIX = "plugin:"

GENERIC_ACTIONS = [
    {"id": ACTION_NONE, "label": "No action"},
    {"id": ACTION_REFRESH, "label": "Refresh"},
    {"id": ACTION_NEXT_PLUGIN, "label": "Next plugin"},
    {"id": ACTION_PREVIOUS_PLUGIN, "label": "Previous plugin"},
    {"id": ACTION_NEXT_PLAYLIST, "label": "Next playlist"},
    {"id": ACTION_PREVIOUS_PLAYLIST, "label": "Previous playlist"},
    {"id": ACTION_PIN, "label": "Pin / unpin"},
    {"id": ACTION_SCREEN_OFF, "label": "Screen off"},
]

DEFAULT_SHORT_ACTIONS = {
    "A": ACTION_PREVIOUS_PLAYLIST,
    "B": ACTION_NEXT_PLAYLIST,
    "C": ACTION_REFRESH,
    "D": ACTION_SCREEN_OFF,
}
DEFAULT_LONG_PRESS_SECONDS = 1.0
DEBOUNCE = timedelta(milliseconds=20)


def default_button_config(pins=None):
    """Returns the button settings used until the user saves their own."""
    pins = pins or {}
    config = {"long_press_seconds": DEFAULT_LONG_PRESS_SECONDS}
    for button in BUTTONS:
        config[button] = {"pin": pins.get(button), "short": DEFAULT_SHORT_ACTIONS[button], "long": ACTION_NONE}
    return config


def get_button_config(device_config):
    """Returns the saved button settings with defaults filled in."""
    saved = device_config.get_config("buttons", default=None) or {}
    config = default_button_config()
    config["long_press_seconds"] = saved.get("long_press_seconds", DEFAULT_LONG_PRESS_SECONDS)
    for button in BUTTONS:
        config[button].update(saved.get(button, {}))
    return config


def is_valid_action(action, playlist_ids=(), plugin_actions=()):
    """Whether action is a generic action, a known playlist, or one of the given plugin action ids."""
    if any(action == a["id"] for a in GENERIC_ACTIONS):
        return True
    if action.startswith(PLAYLIST_ACTION_PREFIX):
        return action[len(PLAYLIST_ACTION_PREFIX):] in playlist_ids
    if action.startswith(PLUGIN_ACTION_PREFIX):
        return action[len(PLUGIN_ACTION_PREFIX):] in plugin_actions
    return False


class PressDetector:
    """Turns press and release times into short and long presses.

    A long press fires as soon as the button has been held for long_press_seconds, and its release
    is then ignored.
    """

    def __init__(self, long_press_seconds):
        self.long_press_seconds = long_press_seconds
        self._pressed = {}  # button -> [press time, long press fired]

    def press(self, button, now):
        self._pressed[button] = [now, False]

    def release(self, button, now):
        """Returns True if this release completes a short press."""
        state = self._pressed.pop(button, None)
        return bool(state and not state[1] and now - state[0] < self.long_press_seconds)

    def poll(self, now):
        """Returns the buttons whose long press fires now."""
        fired = []
        for button, state in self._pressed.items():
            if not state[1] and now - state[0] >= self.long_press_seconds:
                state[1] = True
                fired.append(button)
        return fired

    def next_deadline(self):
        """Returns the monotonic time of the next long press, or None."""
        deadlines = [state[0] + self.long_press_seconds for state in self._pressed.values() if not state[1]]
        return min(deadlines) if deadlines else None


class ButtonService:
    """Reads the configured button pins in a background thread and reports presses to on_press.

    Buttons are active low: each pin uses the Pi's internal pull-up and a press connects it to ground.
    """

    def __init__(self, device_config, on_press):
        self.device_config = device_config
        self.on_press = on_press
        self.thread = None
        self.stop_event = threading.Event()

    def start(self):
        config = get_button_config(self.device_config)
        pins = {button: int(config[button]["pin"]) for button in BUTTONS if config[button].get("pin") is not None}
        if not pins:
            logger.info("No button pins configured.")
            return

        try:
            import gpiod
            import gpiodevice
            from gpiod.line import Bias, Direction, Edge

            chip = gpiodevice.find_chip_by_platform()
            settings = gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP,
                                          edge_detection=Edge.BOTH, debounce_period=DEBOUNCE)
            offsets = {chip.line_offset_from_id(pin): button for button, pin in pins.items()}
            request = chip.request_lines(consumer="inkypi-buttons", config=dict.fromkeys(offsets, settings))
        except Exception:
            logger.warning(f"Could not set up buttons on pins {pins}.", exc_info=True)
            return

        logger.info(f"Listening for buttons on pins {pins}.")
        detector = PressDetector(config["long_press_seconds"])
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, args=(request, offsets, detector), daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread:
            self.stop_event.set()
            self.thread.join()
            self.thread = None

    def reload(self):
        """Applies changed button settings."""
        self.stop()
        self.start()

    def _run(self, request, offsets, detector):
        from gpiod import EdgeEvent

        try:
            while not self.stop_event.is_set():
                deadline = detector.next_deadline()
                timeout = 0.5 if deadline is None else max(0.0, min(0.5, deadline - time.monotonic()))
                if request.wait_edge_events(timeout):
                    for event in request.read_edge_events():
                        button = offsets[event.line_offset]
                        if event.event_type == EdgeEvent.Type.FALLING_EDGE:
                            detector.press(button, time.monotonic())
                        elif detector.release(button, time.monotonic()):
                            self._fire(button, long_press=False)
                for button in detector.poll(time.monotonic()):
                    self._fire(button, long_press=True)
        except Exception:
            logger.exception("Button service stopped after an error.")
        finally:
            request.release()

    def _fire(self, button, long_press):
        logger.info(f"Button {button} {'long' if long_press else 'short'} press")
        try:
            self.on_press(button, long_press)
        except Exception:
            logger.exception(f"Error handling button {button}")
