import json

from launcher.core import util


class Translator:
    _cache = {}

    def __new__(cls, lang_code):
        if lang_code not in cls._cache:
            cls._cache[lang_code] = super(Translator, cls).__new__(cls)
        return cls._cache[lang_code]

    def __init__(self, lang_code):
        if hasattr(self, 'initialized'):
            return
        self.initialized = True
        self.lang_code = lang_code
        self.translations = self.load()

    def load(self):
        lang_file_path = util.get_resource_path("langs", f"{self.lang_code}.json")

        if lang_file_path and lang_file_path.exists():
            with open(lang_file_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        raise FileNotFoundError(f"Translation file not found for language {self.lang_code}")

    def get(self, key, **placeholders):
        translation = self.translations.get(key, key)
        return translation.format(**placeholders)
