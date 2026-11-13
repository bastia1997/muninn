"""User interface language. Texts in the code are written in English and go through `_()`:
    _("Ready.")                              -> translated text
    _("Processed {0} files out of {1}.", ok, n)  -> translated, then filled in with .format()
Translations live in muninn/lang/<code>.json (key = the English text). If one is missing, the English text is used.
The language is read from config.json at import time, before the modules build their texts."""
import json
from pathlib import Path

SOURCE_LANG = "en"
LANGS = {"en": "English", "it": "Italiano", "fr": "Français", "de": "Deutsch", "es": "Español"}
LANG_DIR = Path(__file__).resolve().parent / "lang"

lang = SOURCE_LANG
_cat = {}


def set_lang(code):
    global lang, _cat
    lang = code if code in LANGS else SOURCE_LANG
    _cat = {}
    if lang != SOURCE_LANG:
        try:
            _cat = json.loads((LANG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cat = {}


def _(text, *args, **kw):
    t = _cat.get(text) or text
    if args or kw:
        try:
            return t.format(*args, **kw)
        except (IndexError, KeyError, ValueError):
            return text.format(*args, **kw)
    return t


def guide():
    """Text of the Guide tab in the chosen language (file lang/guide_<code>.txt), otherwise the English one."""
    from .guide import GUIDE
    if lang != SOURCE_LANG:
        p = LANG_DIR / f"guide_{lang}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
    return GUIDE


def _init():
    try:
        from . import config
        set_lang(config.load().get("ui_language", SOURCE_LANG))
    except Exception:
        set_lang(SOURCE_LANG)


_init()
