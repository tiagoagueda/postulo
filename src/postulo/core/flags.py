"""Where a country's flag is, as a URL, or nothing where there is no flag for it.

The `{% flag %}` tag draws one, and two form widgets hand a flag's URL to each option of a
select -- the telephone field's countries and the language picker. Those are forms, below the
template tags; asking the tag library for a URL was an import pointing up (#248). The tag
library registers the same function as its `flag_url` tag.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from django.templatetags.static import static

#: Where `npm run sync:flags` puts the flag-icons flags listed in assets/flags.txt.
FLAG_DIR = Path(__file__).resolve().parents[1] / "static" / "flags"

#: An ISO 3166-1 country, or a 3166-2 subdivision of one: `pt`, or `es-ct` for
#: Catalonia. The second form exists because a language can be at home in a place
#: that is not a state, and Postulo would rather draw that place's own flag than
#: the flag of the state it sits in, which already stands for another language.
_COUNTRY = re.compile(r"[A-Za-z]{2}(?:-[A-Za-z]{2,3})?")


@functools.cache
def _have_flag(country: str) -> bool:
    """Whether ``static/flags/`` holds this country. Cached: the telephone field asks 241
    times a page and the answer changes only when somebody runs ``npm run sync:flags``.

    The name is checked against the pattern first, so nothing a form field carries can be
    turned into a path.
    """
    return bool(_COUNTRY.fullmatch(country)) and (FLAG_DIR / f"{country}.svg").is_file()


def flag_url(country: str) -> str:
    """The static URL of a country's flag, or ``""`` where there is no such flag.

    Empty is a real answer and every caller must handle it: a language with no uncontested
    home gets no flag at all, and a telephone field with nothing chosen yet shows none.

    The file is checked for before ``static()`` is asked for a name, because under the
    manifest storage production uses, asking for a file that was never collected raises
    rather than returning a dead link — right of it, and not something an unrecognised
    country code arriving in a form should be able to trigger.
    """
    country = (country or "").strip().lower()
    return static(f"flags/{country}.svg") if _have_flag(country) else ""
