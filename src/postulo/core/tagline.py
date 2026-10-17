"""The one sentence that says what Postulo is, in every language it speaks.

It lives here, as a translatable string, so that the README's *In your language* table
(``scripts/readme_languages.py``, #937) reads each language's sentence from that language's
catalogue instead of from a list somebody types by hand: a new language arrives with its
sentence, translated in Weblate like every other string.
"""

from django.utils.translation import gettext_noop

#: The text shown for a language in the README. ``gettext_noop`` marks it for extraction
#: without translating it here: the script reads the catalogues itself.
TAGLINE = gettext_noop(
    "Postulo is a self-hosted job application manager: your applications, CVs and cover "
    "letters on your own server, with no paid features."
)
