"""A translated sentence with pieces of markup in it, built so the translation stays text (#806).

``format_html(_("... {link} ..."), link=...)`` makes the *translation* the format string, and
format_html marks what it returns safe. A translator's text then is treated as markup, and a
catalogue entry with ``<script>`` in it, or a ``{link.__class__}`` that str.format would follow,
reaches the page. A catalogue is edited on a translation server by people the project does not
vet one string at a time, so it is data to be escaped, never code to be trusted.

:func:`sentence` escapes the translated text and then fills only the plain ``{name}`` slots,
by name, with the pieces the caller passes, escaped unless they are already safe. A slot the
translation invented, and anything with an attribute, an index or a format spec in it, is left
as written.
"""

from __future__ import annotations

import re

from django.utils.html import conditional_escape, escape
from django.utils.safestring import SafeString, mark_safe

_SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def sentence(template, **parts) -> SafeString:
    """``template`` (translated text) with each ``{name}`` replaced by ``parts[name]``."""
    text = str(escape(str(template)))

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in parts:
            return match.group(0)
        return str(conditional_escape(parts[name]))

    return mark_safe(_SLOT.sub(fill, text))  # noqa: S308 - the text and every part are escaped
