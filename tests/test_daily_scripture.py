import json
from unittest.mock import Mock

import pytest

import plugins.daily_scripture.daily_scripture as scripture_module
from plugins.daily_scripture.daily_scripture import ACTION_NEXT, ACTION_PREVIOUS, BibleQuote


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """A BibleQuote whose AI calls return numbered verses and whose renders return the verse source."""
    plugin = BibleQuote({"id": "daily_scripture"})
    monkeypatch.setattr(plugin, "get_history_file", lambda: str(tmp_path / "history.json"))
    monkeypatch.setattr(plugin, "get_used_quotes_file", lambda: str(tmp_path / "used_quotes.txt"))
    monkeypatch.setattr(plugin, "render_verse", lambda settings, device_config, verse: verse["source"])
    monkeypatch.setattr(scripture_module, "create_text_generator", lambda model, device_config: None)

    plugin.ai_calls = 0
    plugin.avoided = []
    plugin.next_source = None

    def fetch(text_generator, model, version, used_quotes=None):
        plugin.ai_calls += 1
        plugin.avoided.append(used_quotes)
        source = plugin.next_source or f"Verse {plugin.ai_calls}"
        return json.dumps({"text": "...", "source": source, "reason": "..."})

    monkeypatch.setattr(BibleQuote, "fetch_daily_scripture", staticmethod(fetch))
    return plugin


SETTINGS = {"textModel": "claude-haiku-4-5"}
DEVICE = Mock()
DEVICE.get_config.side_effect = lambda key, default=None: {"timezone": "America/Denver"}.get(key, default)


def test_previous_and_next_step_through_history_without_ai_calls(plugin):
    for _ in range(3):
        plugin.generate_image(SETTINGS, DEVICE)
    assert plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE) == "Verse 2"
    assert plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE) == "Verse 1"
    assert plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE) == "Verse 1"  # stays at the oldest
    assert plugin.run_button_action(ACTION_NEXT, SETTINGS, DEVICE) == "Verse 2"
    assert plugin.ai_calls == 3


def test_next_past_the_newest_generates_a_new_verse(plugin):
    plugin.generate_image(SETTINGS, DEVICE)
    assert plugin.run_button_action(ACTION_NEXT, SETTINGS, DEVICE) == "Verse 2"
    assert plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE) == "Verse 1"


def test_previous_with_no_history_generates_a_verse(plugin):
    assert plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE) == "Verse 1"


def test_new_verse_resets_the_cursor(plugin):
    for _ in range(2):
        plugin.generate_image(SETTINGS, DEVICE)
    plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE)
    plugin.generate_image(SETTINGS, DEVICE)
    assert plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE) == "Verse 2"


def test_the_ai_avoids_recent_references_once_each(plugin):
    plugin.next_source = "John 3:16 (ESV)"
    plugin.generate_image(SETTINGS, DEVICE)
    plugin.generate_image(SETTINGS, DEVICE)
    plugin.next_source = None
    plugin.generate_image(SETTINGS, DEVICE)
    assert plugin.avoided[-1] == ["John 3:16 (ESV)"]


def test_used_quotes_file_moves_into_the_history_until_newer_verses_replace_it(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(scripture_module, "HISTORY_SIZE", 3)
    (tmp_path / "used_quotes.txt").write_text("Old 1\nOld 2\nOld 3\n")
    plugin.generate_image(SETTINGS, DEVICE)
    assert not (tmp_path / "used_quotes.txt").exists()
    assert plugin.avoided[-1] == ["Old 1", "Old 2", "Old 3"]
    plugin.generate_image(SETTINGS, DEVICE)
    assert plugin.avoided[-1] == ["Old 2", "Old 3", "Verse 1"]
    plugin.generate_image(SETTINGS, DEVICE)
    plugin.generate_image(SETTINGS, DEVICE)
    assert plugin.avoided[-1] == ["Verse 1", "Verse 2", "Verse 3"]


def test_history_for_display_is_newest_first_and_marks_the_current_verse(plugin):
    for _ in range(3):
        plugin.generate_image(SETTINGS, DEVICE)
    plugin.run_button_action(ACTION_PREVIOUS, SETTINGS, DEVICE)
    history = plugin.get_history_for_display()
    assert [(v["source"], v["current"]) for v in history] == [("Verse 3", False), ("Verse 2", True),
                                                               ("Verse 1", False)]
    assert history[0]["added_at"].startswith("20")


@pytest.mark.parametrize("version,name,deuterocanon", [
    ("ESV", "English Standard Version (ESV)", False),
    ("NABRE", "New American Bible, Revised Edition (NABRE)", True),
])
def test_prompt_names_the_version_and_allows_the_deuterocanon_for_catholic_bibles(version, name, deuterocanon):
    text_generator = Mock()
    BibleQuote.fetch_daily_scripture(text_generator, "model", version)
    user_prompt = text_generator.generate_text.call_args.args[2]
    assert f"using the {name} version" in user_prompt
    assert ("Sirach" in user_prompt) == deuterocanon
