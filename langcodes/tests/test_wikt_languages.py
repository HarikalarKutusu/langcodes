"""
Here, we test that we can associate a language code with each language name
that is commonly used on Wiktionary, that all the language codes are
different, and that each language name matches only one code.
"""
import langcodes
from langcodes.language_lists import WIKT_LANGUAGE_NAMES
import pytest


@pytest.mark.parametrize('target_lang', ['en', 'de'])
def test_wiktionary_languages(target_lang):
    seen_codes = {}
    for lang_name in WIKT_LANGUAGE_NAMES[target_lang]:
        if lang_name.startswith('Proto-'):
            continue
        code = str(langcodes.find(lang_name))
        assert code not in seen_codes, \
            "%r and %r have the same code" % (seen_codes[code], lang_name)
        seen_codes[code] = lang_name
