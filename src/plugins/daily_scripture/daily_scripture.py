from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import resolve_path
from utils.ai_utils import create_text_generator
from utils.image_utils import resize_image
from datetime import datetime, timedelta
import logging
import json
import os
import pytz

logger = logging.getLogger(__name__)

HISTORY_SIZE = 30
ACTION_PREVIOUS = "previous_verse"
ACTION_NEXT = "next_verse"

PROTESTANT = "Protestant (66 books)"
CATHOLIC = "Catholic (73 books, with the Deuterocanon)"
# Translations by canon, Protestant first; each maps its abbreviation to its full name.
BIBLE_VERSIONS = {
    PROTESTANT: {
        "ESV": "English Standard Version",
        "NIV": "New International Version",
        "NASB": "New American Standard Bible",
        "NLT": "New Living Translation",
        "NKJV": "New King James Version",
        "KJV": "King James Version",
        "CSB": "Christian Standard Bible",
        "HCSB": "Holman Christian Standard Bible",
        "NET": "New English Translation",
        "RSV": "Revised Standard Version",
    },
    CATHOLIC: {
        "NABRE": "New American Bible, Revised Edition",
        "RSV-2CE": "Revised Standard Version, Second Catholic Edition",
        "NRSV-CE": "New Revised Standard Version, Catholic Edition",
        "DRA": "Douay-Rheims",
        "NJB": "New Jerusalem Bible",
    },
}

class BibleQuote(BasePlugin):

    def get_button_actions(self):
        return [
            {"id": ACTION_PREVIOUS, "label": "Previous verse"},
            {"id": ACTION_NEXT, "label": "Next verse"},
        ]

    def run_button_action(self, action_id, settings, device_config):
        """Steps through recent verses without calling the AI, generating a new verse past the newest."""
        history = self.load_history()
        entries = history["entries"]
        current = history["cursor"] if history["cursor"] is not None else len(entries) - 1

        if action_id == ACTION_PREVIOUS:
            if not entries:
                return self.generate_image(settings, device_config)
            current = max(current - 1, 0)
        elif action_id == ACTION_NEXT:
            if current >= len(entries) - 1:
                return self.generate_image(settings, device_config)
            current += 1
        else:
            return super().run_button_action(action_id, settings, device_config)

        history["cursor"] = current
        self.save_history(history)
        return self.render_verse(settings, device_config, entries[current])

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenAI or Anthropic",
            "expected_key": "OPEN_AI_SECRET or ANTHROPIC_API_KEY"
        }
        template_params['style_settings'] = True
        template_params['bible_versions'] = BIBLE_VERSIONS
        template_params['verse_history'] = self.get_history_for_display()
        template_params['older_verse_sources'] = list(reversed(self.load_history()["legacy_sources"]))
        return template_params

    def generate_image(self, settings, device_config):
        text_model = settings.get('textModel')
        if not text_model:
            raise RuntimeError("Text Model is required.")

        bible_version = settings.get('bibleVersion', 'ESV')

        try:
            text_generator = create_text_generator(text_model, device_config)
            used_quotes = self.get_used_quotes()
            logger.info(f"Retrieved {len(used_quotes)} previously used quotes: {used_quotes}")
            bible_response = self.fetch_daily_scripture(text_generator, text_model, bible_version, used_quotes)
        except Exception as e:
            logger.error(f"Failed to make AI request: {str(e)}")
            raise RuntimeError("AI request failure, please check logs.")

        # Parse the JSON response
        try:
            # Remove markdown code blocks if present
            json_response = bible_response.strip()
            if json_response.startswith('```json'):
                json_response = json_response[7:]  # Remove ```json
            if json_response.endswith('```'):
                json_response = json_response[:-3]  # Remove ```
            json_response = json_response.strip()

            bible_data = json.loads(json_response)
            text = bible_data.get('text', '')
            source = bible_data.get('source', '')
            reason = bible_data.get('reason', '')

            # Check if AI provided a repeat scripture it was told to avoid
            if source and source in used_quotes:
                logger.warning(f"AI model provided a repeat scripture that was in the exclusion list: {source}")

        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {bible_response}")
            # Fallback to treating the response as plain text
            text = bible_response
            source = ""
            reason = ""

        verse = {"text": text, "source": source, "reason": reason}
        if source:
            timezone = pytz.timezone(device_config.get_config("timezone", default="UTC") or "UTC")
            self.add_to_history({**verse, "added_at": datetime.now(timezone).isoformat()})
        return self.render_verse(settings, device_config, verse)

    def render_verse(self, settings, device_config, verse):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        image_template_params = {
            "title": settings.get("title", "Daily Scripture") or "Daily Scripture",
            "text": verse["text"],
            "source": verse["source"],
            "reason": verse["reason"],
            "bible_icon": self.get_plugin_dir('icons/color-icon.svg'),
            "plugin_settings": settings
        }

        return self.render_image(dimensions, "daily_scripture.html", "daily_scripture.css", image_template_params)

    def get_history_file(self):
        return os.path.join(self.get_plugin_dir(), 'history.json')

    def get_used_quotes_file(self):
        """The list of recent references that older versions kept apart from the history."""
        return os.path.join(self.get_plugin_dir(), 'used_quotes.txt')

    def load_history(self):
        """Returns recent verses, oldest first; the cursor of the one being shown (None for the newest);
        and older references that only have a source, from before the history kept full verses."""
        history = {"entries": [], "cursor": None, "legacy_sources": []}
        try:
            with open(self.get_history_file(), 'r', encoding='utf-8') as f:
                saved = json.load(f)
            history.update({key: saved[key] for key in history if key in saved})
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to read verse history: {e}")
        return self._merge_used_quotes(history)

    def _merge_used_quotes(self, history):
        """Moves the references in used_quotes.txt into the history, then deletes the file."""
        used_quotes_file = self.get_used_quotes_file()
        if not os.path.exists(used_quotes_file):
            return history
        try:
            with open(used_quotes_file, 'r', encoding='utf-8') as f:
                sources = [line.strip() for line in f if line.strip()]
            known = set(history["legacy_sources"]) | {entry.get("source") for entry in history["entries"]}
            history["legacy_sources"] += [source for source in sources if source not in known]
            self._trim_legacy_sources(history)
            self.save_history(history)
            os.remove(used_quotes_file)
            logger.info(f"Moved {len(sources)} references from used_quotes.txt into the verse history.")
        except Exception as e:
            logger.warning(f"Failed to move used quotes into the verse history: {e}")
        return history

    @staticmethod
    def _trim_legacy_sources(history):
        """Keeps older references only until newer verses fill the history."""
        room = max(HISTORY_SIZE - len(history["entries"]), 0)
        history["legacy_sources"] = history["legacy_sources"][-room:] if room else []

    def save_history(self, history):
        try:
            with open(self.get_history_file(), 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save verse history: {e}")

    def add_to_history(self, verse):
        history = self.load_history()
        history["entries"] = (history["entries"] + [verse])[-HISTORY_SIZE:]
        history["cursor"] = None
        self._trim_legacy_sources(history)
        self.save_history(history)

    def get_used_quotes(self):
        """Returns the references of recent verses, oldest first, for the AI to avoid repeating."""
        history = self.load_history()
        sources = history["legacy_sources"] + [entry.get("source") for entry in history["entries"]]
        return list(dict.fromkeys(source for source in sources if source))

    def get_history_for_display(self):
        """Returns recent verses, newest first, marking the one the buttons are on."""
        history = self.load_history()
        entries = history["entries"]
        current = history["cursor"] if history["cursor"] is not None else len(entries) - 1
        return [{**entry, "current": index == current} for index, entry in reversed(list(enumerate(entries)))]
    
    @classmethod
    def fetch_daily_scripture(cls, text_generator, model, bible_version, used_quotes=None):
        """
        Fetch Bible quote using the AI utility class.
        """
        logger.info(f"Getting Bible quote, version: {bible_version}, model: {model}")

        today_date = datetime.today().strftime('%Y-%m-%d')
        
        # Build the system prompt with used quotes avoidance
        system_content = (
            "You are a helpful Bible scholar and will be providing a passage from the Bible. "
        )
        
        system_content += (
            "You MUST format your response as valid JSON (with no other information) with the following fields: "
            "text: The passage from the Bible passage (keep it concise, preferably under 40 words), "
            "source: The location of that passage in the Bible formatted as '<book> <chapter>:<verse_start>[-<verse_end>] (<bible_version>)', "
            "reason: A short description (preferable under 40 words) of why this passage is relevant, inspirational, hopeful, or redeeming. "
            f"For reference, today is {today_date}. "
        )

        catholic_names = BIBLE_VERSIONS[CATHOLIC]
        version_name = (catholic_names | BIBLE_VERSIONS[PROTESTANT]).get(bible_version)
        version = f"{version_name} ({bible_version})" if version_name else bible_version
        user_content = (
            f"Please provide a short Bible quote using the {version} version of the Bible. "
            # "The quote should show assurance of pardon or be convicting, inspiring, encouraging, or hopeful. "
            # "If Christmas, Easter, or Passover is currently happening or will happen in the next 2 weeks, "
            # "choose a quote related to that holiday. If there is another Christian holiday in the next 3 days, "
            # "choose a quote related to that holiday. "
        )
        if bible_version in catholic_names:
            user_content += (
                "This is a Catholic Bible, so the passage may also come from the deuterocanonical books: "
                "Tobit, Judith, Wisdom, Sirach, Baruch, 1 and 2 Maccabees, and the additions to Esther and Daniel. "
            )

        # Add used quotes avoidance if we have previous quotes
        if used_quotes:
            logger.info(f"Instructing AI to avoid {len(used_quotes)} previously used passages")
            user_content += (
                f"IMPORTANT: DO NOT provide any of these previously used Bible passages: [{', '.join(used_quotes)}]. "
                "Please select a different passage that has not been used recently. "
            )

        # Note: We'll use a moderate temperature to encourage more varied responses while
        # still maintaining coherence. Higher temperature helps avoid repetitive quotes.
        quote_response = text_generator.generate_text(model, system_content, user_content, temperature=0.75)
        logger.info(f"Generated Bible quote response: {quote_response}")

        return quote_response