"""What every file read back in through a review page has in common (#659).

The candidate file (`resume.candidate`, #181) and the files of one kind of record
(`core.kind_files`) share a stance: reading somebody else's file is guesswork, so it is
read, shown, and added only when somebody says so. What is shared lives here: the outcomes
a row of such a file can have and what the page calls them, the refusal that stops a file
from being read at all, and the few ways a value is held between reading and confirming.

Nothing here looks at the database.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy

# --------------------------------------------------------------------- what may happen

ADD = "add"
PRESENT = "present"
KEPT = "kept"
REFUSED = "refused"
REPEATED = "repeated"

#: What the page calls each of them, in the order it counts them.
OUTCOMES = {
    ADD: gettext_lazy("Will be added"),
    PRESENT: gettext_lazy("Already in your record"),
    KEPT: gettext_lazy("Yours is kept"),
    REPEATED: gettext_lazy("In the file twice"),
    REFUSED: gettext_lazy("Cannot be added"),
}


class Refused(Exception):
    """The file will not be read, and the message says why, to the person who chose it."""


def no_constants(name: str):
    """`NaN` and `Infinity` are not JSON, and nothing a career or a search says is either."""
    raise ValueError(name)


def short(value, limit: int = 40) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def kept(value):
    """A value as it will be held: text, a whole number, a yes or a no, or nothing.

    Anything else -- a list where a title should be, an object, a fraction -- is held as an
    empty list, which says *something was here and it was not text* without holding
    whatever it was. What a session keeps is then as flat as a file's rows are.
    """
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value
    return []
