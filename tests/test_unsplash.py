import threading
from unittest.mock import Mock

import pytest
from PIL import Image

import plugins.unsplash.unsplash as unsplash_module
from plugins.unsplash.unsplash import CURRENT_PHOTO_KEY, RANDOM_URL, SEARCH_URL, Unsplash


def photo(photo_id):
    url = f"https://img/{photo_id}"
    return {"id": photo_id, "urls": {"full": url, "regular": url, "small": url}}


def response(status, body):
    result = Mock(status_code=status)
    result.json.return_value = body
    result.raise_for_status.side_effect = None if status < 400 else RuntimeError(f"HTTP {status}")
    return result


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """An Unsplash plugin whose API calls are answered by `harness.answer(url, params)`."""
    plugin = Unsplash({"id": "unsplash"})
    plugin.image_loader = Mock()
    plugin.image_loader.from_url.side_effect = lambda url, *args, **kwargs: Image.new("RGB", (8, 8))
    monkeypatch.setattr(plugin, "get_hidden_photos_file", lambda: str(tmp_path / "hidden_photos.json"))
    session = Mock()
    monkeypatch.setattr(unsplash_module, "get_http_session", lambda: session)
    monkeypatch.setattr(unsplash_module, "_is_low_resource_device", lambda: False)
    monkeypatch.setattr(unsplash_module.random, "sample", lambda items, count: list(items))

    device_config = Mock()
    device_config.load_env_key.return_value = "key"
    device_config.get_resolution.return_value = (800, 480)
    device_config.get_config.return_value = "horizontal"
    device_config.lock = threading.RLock()

    class Harness:
        calls = []
        answer = None

        def run(self, settings, action=None):
            if action:
                plugin.run_button_action(action, settings, device_config)
            else:
                plugin.generate_image(settings, device_config)
            return plugin.image_loader.from_url.call_args.args[0]

    harness = Harness()
    harness.plugin = plugin
    session.get.side_effect = lambda url, params, timeout: (harness.calls.append((url, params)),
                                                            harness.answer(url, params))[1]
    return harness


def random_photos(*ids):
    return lambda url, params: response(200, [photo(i) for i in ids])


def test_a_plain_search_uses_the_random_endpoint(harness):
    harness.answer = random_photos("a", "b")
    settings = {"search_query": "wildlife"}
    assert harness.run(settings) == "https://img/a"
    assert settings[CURRENT_PHOTO_KEY] == "a"
    assert [(url, params.get("query")) for url, params in harness.calls] == [(RANDOM_URL, "wildlife")]


@pytest.mark.parametrize("extra", [{"color": "green"}, {"collections": "123"}])
def test_filters_the_random_endpoint_ignores_use_a_random_search_page(harness, monkeypatch, extra):
    monkeypatch.setattr(unsplash_module.random, "randint", lambda low, high: high)

    def answer(url, params):
        page = params.get("page", 1)
        return response(200, {"total": 120, "total_pages": 4, "results": [photo(f"page{page}")]})

    harness.answer = answer
    assert harness.run({"search_query": "wildlife", **extra}) == "https://img/page4"
    assert [(url, params.get("page"), params["per_page"]) for url, params in harness.calls] == \
        [(SEARCH_URL, None, 30), (SEARCH_URL, 4, 30)]


def test_an_empty_deep_page_falls_back_to_page_one(harness, monkeypatch):
    monkeypatch.setattr(unsplash_module.random, "randint", lambda low, high: high)

    def answer(url, params):
        results = [photo("first")] if "page" not in params else []
        return response(200, {"total": 120, "total_pages": 4, "results": results})

    harness.answer = answer
    assert harness.run({"search_query": "wildlife", "color": "green"}) == "https://img/first"


@pytest.mark.parametrize("settings", [{"search_query": "nothing"}, {"search_query": "nothing", "color": "green"}])
def test_no_matches_says_so(harness, settings):
    def answer(url, params):
        if url == RANDOM_URL:
            return response(404, {"errors": ["No photos found."]})
        return response(200, {"total": 0, "total_pages": 0, "results": []})

    harness.answer = answer
    with pytest.raises(RuntimeError, match="No images found"):
        harness.run(settings)


def test_hiding_a_photo_skips_it_from_then_on(harness):
    harness.answer = random_photos("a", "b")
    settings = {"search_query": "wildlife"}
    harness.run(settings)

    assert harness.run(settings, action="hide_photo") == "https://img/b"
    assert harness.plugin.load_hidden_photos() == {"a"}
    assert harness.run(settings) == "https://img/b"


def test_new_instances_hide_photos_with_a_long_press_of_a(harness):
    assert harness.plugin.get_default_button_actions() == {"A": "plugin:hide_photo"}


class TestAiCheck:

    @pytest.fixture
    def answers(self, monkeypatch):
        """The AI's answer for each photo URL, defaulting to yes."""
        answers = {}
        generator = Mock()
        generator.answer_yes_no.side_effect = lambda model, question, url: answers.get(url, True)
        monkeypatch.setattr(unsplash_module, "create_text_generator", lambda model, device_config: generator)
        return answers

    SETTINGS = {"search_query": "wildlife", "aiCheck": "true", "aiCheckModel": "claude-haiku-4-5"}

    def test_skips_photos_the_ai_rejects(self, harness, answers):
        harness.answer = random_photos("a", "b", "c")
        answers["https://img/a"] = False
        assert harness.run(dict(self.SETTINGS)) == "https://img/b"

    def test_fails_when_every_photo_is_rejected(self, harness, answers):
        harness.answer = random_photos("a", "b")
        answers.update({"https://img/a": False, "https://img/b": False})
        with pytest.raises(RuntimeError, match="None of 2 photos passed"):
            harness.run(dict(self.SETTINGS))

    def test_is_skipped_when_off_or_without_a_search(self, harness, answers):
        harness.answer = random_photos("a")
        answers["https://img/a"] = False
        assert harness.run({**self.SETTINGS, "aiCheck": None}) == "https://img/a"
        assert harness.run({**self.SETTINGS, "search_query": ""}) == "https://img/a"
