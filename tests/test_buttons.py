from unittest.mock import Mock

from src.buttons import (ACTION_NONE, ACTION_REFRESH, PressDetector, default_button_config, get_button_config,
                         is_valid_action)


class TestPressDetector:

    def test_quick_release_is_a_short_press(self):
        detector = PressDetector(long_press_seconds=1.0)
        detector.press("A", 10.0)
        assert detector.poll(10.5) == []
        assert detector.release("A", 10.5)

    def test_long_press_fires_while_held_and_its_release_is_ignored(self):
        detector = PressDetector(long_press_seconds=1.0)
        detector.press("A", 10.0)
        assert detector.next_deadline() == 11.0
        assert detector.poll(11.0) == ["A"]
        assert detector.poll(12.0) == []  # fires once
        assert detector.next_deadline() is None
        assert not detector.release("A", 12.5)

    def test_buttons_are_independent(self):
        detector = PressDetector(long_press_seconds=1.0)
        detector.press("A", 10.0)
        detector.press("B", 10.6)
        assert detector.poll(11.0) == ["A"]
        assert detector.release("B", 11.2)

    def test_release_without_press_is_ignored(self):
        assert not PressDetector(1.0).release("C", 5.0)


class TestButtonConfig:

    def test_defaults_fill_in_missing_settings(self):
        device_config = Mock()
        device_config.get_config.return_value = {"C": {"pin": 25, "long": "pin"}}
        config = get_button_config(device_config)
        assert config["C"] == {"pin": 25, "short": ACTION_REFRESH, "long": "pin"}
        assert config["A"]["pin"] is None
        assert config["long_press_seconds"] == 1.0

    def test_default_config_uses_the_given_pins(self):
        config = default_button_config({"A": 5, "D": 24})
        assert config["A"]["pin"] == 5
        assert config["B"]["pin"] is None
        assert config["D"]["long"] == ACTION_NONE

    def test_valid_actions(self):
        assert is_valid_action("next_playlist")
        assert is_valid_action("playlist:abc", playlist_ids=["abc"])
        assert not is_valid_action("playlist:gone", playlist_ids=["abc"])
        assert is_valid_action("plugin:previous_verse", plugin_actions=["previous_verse"])
        assert not is_valid_action("plugin:previous_verse")
        assert not is_valid_action("reboot")
