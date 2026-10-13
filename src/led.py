"""Status LED, such as the one on the back of Spectra 6 Inky Impressions.

The LED fades with hardware PWM when the pin's PWM overlay is loaded (see install/led_dimming.sh), and is
switched on and off through libgpiod otherwise.
"""
import glob
import logging
import math
import os
import shutil
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

LED_USES = ["busy", "long_press", "error", "startup"]
DEFAULT_MAX_BRIGHTNESS = 100

FLASH_SECONDS = 0.15
ERROR_SECONDS = 10
BREATHE_SECONDS = 3.0
STARTUP_BLINK_SECONDS = 1.0
# Error pattern within each cycle: a long flash, a gap, a short flash, then a longer gap.
ERROR_CYCLE = [(0.8, 1.0), (0.2, 0.0), (0.2, 1.0), (0.4, 0.0)]

PWM_SYSFS = "/sys/class/pwm"
PWM_PERIOD_NS = 1_000_000  # 1 kHz, too fast to see flicker
# PWM controllers by the end of their sysfs device name, with the channel each GPIO uses. The first is the
# BCM2835-2711 controller (e.g. 3f20c000.pwm on a Zero 2 W, fe20c000.pwm on a Pi 4), the second the Pi 5's
# RP1 PWM0.
PWM_CONTROLLERS = {
    "20c000.pwm": {12: 0, 13: 1, 18: 0, 19: 1},
    "98000.pwm": {12: 0, 13: 1, 18: 2, 19: 3},
}


def default_led_config(pin=None):
    """Returns the LED settings used until the user saves their own."""
    return {"pin": pin, "max_brightness": DEFAULT_MAX_BRIGHTNESS, **{use: True for use in LED_USES}}


def get_led_config(device_config):
    """Returns the saved LED settings with defaults filled in."""
    return {**default_led_config(), **(device_config.get_config("led", default=None) or {})}


def pwm_channel(device_name, pin):
    """Returns the PWM channel a pin uses on the controller with this sysfs device name, or None."""
    for suffix, channels in PWM_CONTROLLERS.items():
        if device_name.endswith(suffix):
            return channels.get(pin)
    return None


def led_level(state, now, config, dimmable):
    """Returns the LED level from 0 to 1 for the current state, before max brightness is applied."""
    if config["long_press"] and now < state["flash_until"]:
        return 1.0
    if config["error"] and now < state["error_until"]:
        elapsed = (now - state["error_started"]) % sum(duration for duration, _ in ERROR_CYCLE)
        for duration, level in ERROR_CYCLE:
            if elapsed < duration:
                return level
            elapsed -= duration
    if config["busy"] and state["busy_since"] is not None:
        if not dimmable:
            return 1.0
        # Starts at full brightness so the LED reacts the moment work starts, then fades out and back in.
        return (1 + math.cos(2 * math.pi * (now - state["busy_since"]) / BREATHE_SECONDS)) / 2
    if config["startup"] and state["starting_since"] is not None:
        return 1.0 if (now - state["starting_since"]) % (2 * STARTUP_BLINK_SECONDS) < STARTUP_BLINK_SECONDS else 0.0
    return 0.0


class PwmLed:
    """Drives the LED through a sysfs PWM channel, so it can dim."""
    dimmable = True

    def __init__(self, chip, channel):
        self.path = os.path.join(chip, f"pwm{channel}")
        if not os.path.isdir(self.path):
            self._write(os.path.join(chip, "export"), channel)
            # udev can take a moment to finish setting up the exported channel.
            for _ in range(20):
                if os.path.exists(os.path.join(self.path, "enable")):
                    break
                time.sleep(0.05)
        self._write(os.path.join(self.path, "duty_cycle"), 0)
        self._write(os.path.join(self.path, "period"), PWM_PERIOD_NS)
        self._write(os.path.join(self.path, "enable"), 1)
        self._duty = 0

    def set(self, level):
        duty = int(PWM_PERIOD_NS * level)
        if duty != self._duty:
            self._write(os.path.join(self.path, "duty_cycle"), duty)
            self._duty = duty

    def close(self):
        self.set(0)
        self._write(os.path.join(self.path, "enable"), 0)

    @staticmethod
    def _write(path, value):
        with open(path, "w") as f:
            f.write(str(value))


class GpioLed:
    """Switches the LED on and off through libgpiod when the pin has no PWM."""
    dimmable = False

    def __init__(self, pin):
        import gpiod
        import gpiodevice
        from gpiod.line import Direction, Value

        self._values = (Value.INACTIVE, Value.ACTIVE)
        chip = gpiodevice.find_chip_by_platform()
        self.offset = chip.line_offset_from_id(pin)
        settings = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)
        self.request = chip.request_lines(consumer="inkypi-led", config={self.offset: settings})
        self._on = False

    def set(self, level):
        on = level >= 0.5
        if on != self._on:
            self.request.set_value(self.offset, self._values[on])
            self._on = on

    def close(self):
        self.set(0)
        self.request.release()


def find_pwm(pin):
    """Returns (chip path, channel) when the pin is set up for hardware PWM, or None."""
    # The PWM controller only exists when an overlay enables it, and that overlay may route it to another pin.
    if shutil.which("pinctrl"):
        result = subprocess.run(["pinctrl", "get", str(pin)], capture_output=True, text=True)
        if "PWM" not in result.stdout:
            return None
    for chip in sorted(glob.glob(os.path.join(PWM_SYSFS, "pwmchip*"))):
        channel = pwm_channel(os.path.basename(os.path.realpath(os.path.join(chip, "device"))), pin)
        if channel is not None:
            return chip, channel
    return None


class LedController:
    """Shows InkyPi's state on the LED from a background thread.

    Patterns, highest priority first: a short flash when a long press is reached, a long-short pattern for
    ERROR_SECONDS after an error, breathing (or solid without PWM) while busy, and a slow blink while
    starting up.
    """

    def __init__(self, device_config):
        self.device_config = device_config
        self.condition = threading.Condition()
        self.state = {"flash_until": 0.0, "error_until": 0.0, "error_started": 0.0, "busy_since": None,
                      "starting_since": time.monotonic()}
        self.led = None
        self.status = "No LED pin set"
        self.thread = None
        self.running = False

    def start(self):
        config = get_led_config(self.device_config)
        self.config = config
        pin = config["pin"]
        if pin is None:
            self.status = "No LED pin set"
            return

        pin = int(pin)
        try:
            pwm = find_pwm(pin)
            if pwm:
                self.led = PwmLed(*pwm)
                self.status = f"Dimming with hardware PWM on GPIO {pin}"
            else:
                self.led = GpioLed(pin)
                self.status = f"On and off only on GPIO {pin}; dimming needs its PWM overlay"
        except Exception:
            logger.warning(f"Could not set up the LED on GPIO {pin}.", exc_info=True)
            self.led = None
            self.status = f"Could not use GPIO {pin}, see the logs"
            return

        logger.info(f"LED: {self.status}.")
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread:
            with self.condition:
                self.running = False
                self.condition.notify_all()
            self.thread.join()
            self.thread = None
        if self.led:
            try:
                self.led.close()
            except Exception:
                logger.warning("Could not turn the LED off.", exc_info=True)
            self.led = None

    def reload(self):
        """Applies changed LED settings."""
        self.stop()
        self.start()

    def set_busy(self, busy):
        with self.condition:
            if busy != (self.state["busy_since"] is not None):
                self.state["busy_since"] = time.monotonic() if busy else None
                self.condition.notify_all()

    def set_starting(self, starting):
        with self.condition:
            if starting != (self.state["starting_since"] is not None):
                self.state["starting_since"] = time.monotonic() if starting else None
                self.condition.notify_all()

    def flash(self):
        with self.condition:
            self.state["flash_until"] = time.monotonic() + FLASH_SECONDS
            self.condition.notify_all()

    def error(self):
        with self.condition:
            self.state["error_started"] = time.monotonic()
            self.state["error_until"] = self.state["error_started"] + ERROR_SECONDS
            self.condition.notify_all()

    def clear_error(self):
        with self.condition:
            self.state["error_until"] = 0.0
            self.condition.notify_all()

    def _run(self):
        try:
            with self.condition:
                while self.running:
                    now = time.monotonic()
                    level = led_level(self.state, now, self.config, self.led.dimmable)
                    self.led.set(level * self.config["max_brightness"] / 100 if self.led.dimmable else level)
                    animated = (now < max(self.state["flash_until"], self.state["error_until"])
                                or self.state["busy_since"] is not None
                                or self.state["starting_since"] is not None)
                    self.condition.wait(timeout=0.03 if animated else None)
        except Exception:
            logger.exception("LED stopped after an error.")
