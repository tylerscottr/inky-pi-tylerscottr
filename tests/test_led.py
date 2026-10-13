from unittest.mock import Mock

import pytest

from src.led import (ERROR_SECONDS, default_led_config, get_led_config, led_level, pwm_channel)

ALL_ON = default_led_config(13)


def state(**overrides):
    return {"flash_until": 0.0, "error_until": 0.0, "error_started": 0.0, "busy_since": None,
            "starting_since": None, **overrides}


class TestPatterns:

    def test_off_when_idle(self):
        assert led_level(state(), 100.0, ALL_ON, dimmable=True) == 0.0

    def test_long_press_flash_beats_everything(self):
        s = state(flash_until=100.1, error_until=110.0, error_started=99.0, busy_since=99.5)
        assert led_level(s, 100.0, ALL_ON, dimmable=True) == 1.0

    @pytest.mark.parametrize(
        "elapsed,expected",
        [(0.1, 1.0), (0.7, 1.0), (0.9, 0.0), (1.1, 1.0), (1.3, 0.0), (1.7, 1.0)],  # long, gap, short, gap, repeat
    )
    def test_error_is_a_long_then_short_flash(self, elapsed, expected):
        s = state(error_started=100.0, error_until=100.0 + ERROR_SECONDS)
        assert led_level(s, 100.0 + elapsed, ALL_ON, dimmable=True) == expected

    def test_error_beats_busy_until_it_expires(self):
        s = state(error_started=100.0, error_until=100.0 + ERROR_SECONDS, busy_since=100.0)
        assert led_level(s, 100.9, ALL_ON, dimmable=False) == 0.0  # the error pattern's gap
        assert led_level(s, 100.0 + ERROR_SECONDS, ALL_ON, dimmable=False) == 1.0  # busy again

    def test_busy_breathes_with_pwm_and_is_solid_without(self):
        s = state(busy_since=100.0)
        assert led_level(s, 100.0, ALL_ON, dimmable=True) == pytest.approx(1.0)  # full brightness right away
        assert led_level(s, 101.5, ALL_ON, dimmable=True) == pytest.approx(0.0)
        assert led_level(s, 103.0, ALL_ON, dimmable=True) == pytest.approx(1.0)
        assert 0.2 < led_level(s, 100.6, ALL_ON, dimmable=True) < 0.8
        assert led_level(s, 100.0, ALL_ON, dimmable=False) == 1.0

    def test_startup_blinks(self):
        s = state(starting_since=100.0)
        assert led_level(s, 100.5, ALL_ON, dimmable=True) == 1.0
        assert led_level(s, 101.5, ALL_ON, dimmable=True) == 0.0

    def test_unchecked_uses_are_ignored(self):
        config = {**ALL_ON, "busy": False, "error": False}
        s = state(error_started=100.0, error_until=110.0, busy_since=100.0, starting_since=100.0)
        assert led_level(s, 100.5, config, dimmable=True) == 1.0  # the startup blink shows through
        assert led_level(s, 101.5, config, dimmable=True) == 0.0


class TestConfig:

    def test_defaults_turn_every_use_on(self):
        device_config = Mock()
        device_config.get_config.return_value = {"pin": 13, "busy": False}
        config = get_led_config(device_config)
        assert config["pin"] == 13
        assert config["busy"] is False
        assert config["error"] and config["long_press"] and config["startup"]
        assert config["max_brightness"] == 100

    @pytest.mark.parametrize(
        "device,pin,expected",
        [
            ("3f20c000.pwm", 13, 1),   # Zero 2 W / Pi 3
            ("fe20c000.pwm", 12, 0),   # Pi 4
            ("1f00098000.pwm", 13, 1),  # Pi 5 RP1 PWM0
            ("1f00098000.pwm", 18, 2),
            ("1f0009c000.pwm", 13, None),  # Pi 5 fan controller
            ("fe20c800.pwm", 13, None),    # Pi 4's second controller
            ("3f20c000.pwm", 5, None),     # not a PWM pin
        ],
    )
    def test_pwm_channel(self, device, pin, expected):
        assert pwm_channel(device, pin) == expected
