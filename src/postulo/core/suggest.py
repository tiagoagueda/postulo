"""One way for a text field to suggest what is about to be typed (#1251).

A field names a **kind** (``company``, ``job-title``, ``location``); the kind names a
**suggester**, a function that is given who is asking and what has been typed and answers
with a short list of strings. The page script (`assets/js/app.js`, "suggesting") asks
`core:suggest` for the list as the person pauses, and draws it as an ARIA combobox under the
box. Nothing here validates anything: a suggestion is offered, never imposed, and the box
stays a text box that posts what was typed.

**One registry.** `register` is the whole of the contract, so a plugin adds a kind by calling
it with a name of its own (``"<plugin>.<kind>"``) and a function, and a field of its own then
names it with `SuggestInput`. The built-in kinds are registered by the apps that own the data
(`jobs.suggesters`, `resume.suggesters`).

**The person's own data first.** A suggester reads what that account recorded and nothing of
anybody else's: it is given the user, never a queryset over the instance. Reference data
(the ESCO titles) follows the person's own entries. A lookup that goes to a third party is
not a suggester of this kind: this contract has nothing to send anywhere, and what is typed
into a suggestion box is never passed on.

**What a choice fills (#1251).** A kind may also say what follows from choosing one of its
values: ``register(kind, source, fills=...)``. ``fills(user, chosen)`` answers with
``{key: text}`` from that person's own records, and the box maps a key to the field beside it
(`SuggestInput(fills=...)`). The page asks once, when an option is taken (`core:suggest_fill`),
fills only fields that are empty, says what it filled and offers Undo. Nothing typed is ever
overwritten, and an answer that cannot be given is no answer.

**Privacy.** Records are read through the account (`for_user`), so what is offered is what
the person owns or already sees through a group they belong to, and never another account's.
Nothing typed here is sent to a third party. A kind that *is* a lookup at a third party (a
register, Wikidata: #879) must say so with ``register(..., enabled=...)``: it is not called,
and answers nothing, until the person has switched it on; and like every kind it is asked only
after a pause in typing, never on page load, and within the per-account rate of
`POSTULO_SUGGESTION_RATE`.

**Matching** is by the box's own words, ignoring case and accents: the entries that begin
with what was typed come first, then the ones with a word that begins with it, then the ones
that contain it. Inside each group the order the suggester gave -- most used first -- is
kept. At most `AT_MOST` are returned: a list to choose from, not a directory.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django import forms
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

#: The most a suggestion box shows (#1251).
AT_MOST = 10

#: The most that is read of what was typed. A name never needs more.
TYPED_AT_MOST = 100

#: How a kind is spelled: lower case words joined by hyphens, a plugin's with a dot after its
#: own name. It is a path segment of the address, so nothing else is welcome.
KIND = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)?$")


@dataclass(frozen=True)
class Suggester:
    """What answers for one kind."""

    kind: str
    #: ``source(user, typed) -> iterable of str``: the candidates, most useful first. They
    #: are narrowed to what was typed by `suggest`, so a source need not do it; one that can
    #: do it better (a database that can search) may, and gets the same matching again.
    source: Callable[[object, str], Iterable[str]]
    #: ``fills(user, chosen) -> {key: text}``: what is known, from this person's own records,
    #: about a value that has been chosen, keyed by what it is (``location``, ``postcode``).
    #: Asked once, when an option is taken -- never as somebody types. None: nothing follows.
    fills: Callable[[object, str], dict[str, str]] | None = None
    #: ``enabled(user) -> bool`` for a kind whose answers come from a lookup the person has to
    #: switch on (a register, Wikidata: #879). None: the person's own entries, always on.
    #: A kind that is not enabled for somebody answers nothing and is never called.
    enabled: Callable[[object], bool] | None = None


_registry: dict[str, Suggester] = {}


def register(
    kind: str,
    source: Callable[[object, str], Iterable[str]],
    *,
    fills: Callable[[object, str], dict[str, str]] | None = None,
    enabled: Callable[[object], bool] | None = None,
) -> None:
    """Add a kind, or replace the one of that name.

    ``fills`` says what follows from choosing a value (see `Suggester`); ``enabled`` is for a
    kind that is a lookup and must be switched on by the person, as the issue lays down: it
    is not asked, and answers nothing, until it is.
    """
    if not KIND.match(kind):
        raise ValueError(f"A suggestion kind is lower case words and hyphens, not {kind!r}.")
    _registry[kind] = Suggester(kind, source, fills, enabled)


def kinds() -> list[str]:
    return sorted(_registry)


def known(kind: str) -> bool:
    return kind in _registry


def folded(text: str) -> str:
    """``text`` without case or accents, for comparing and nothing else."""
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def matching(candidates: Iterable[str], typed: str, limit: int = AT_MOST) -> list[str]:
    """The candidates that fit ``typed``, the beginnings first, each once, at most ``limit``."""
    wanted = folded(typed.strip())
    seen: set[str] = set()
    starts: list[str] = []
    words: list[str] = []
    inside: list[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        key = folded(value)
        if not value or key in seen:
            continue
        seen.add(key)
        if not wanted or key.startswith(wanted):
            starts.append(value)
        elif any(word.startswith(wanted) for word in re.split(r"[\s,;/()-]+", key)):
            words.append(value)
        elif wanted in key:
            inside.append(value)
        if len(starts) >= limit and not wanted:
            break
    return (starts + words + inside)[:limit]


def suggest(kind: str, user, typed: str = "") -> list[str]:
    """What ``kind`` suggests to ``user`` for ``typed``; nothing for a kind not registered
    and nothing for somebody who is not signed in."""
    suggester = _registry.get(kind)
    if suggester is None or not usable(suggester, user):
        return []
    typed = (typed or "")[:TYPED_AT_MOST]
    return matching(suggester.source(user, typed), typed)


def usable(suggester: Suggester, user) -> bool:
    """Whether ``user`` may be answered by ``suggester``: signed in, and, for a lookup, with
    it switched on. A switch that cannot be read is off."""
    if not getattr(user, "is_authenticated", False):
        return False
    if suggester.enabled is None:
        return True
    try:
        return bool(suggester.enabled(user))
    except Exception:
        return False


def fills(kind: str, user, chosen: str) -> dict[str, str]:
    """What choosing ``chosen`` in a box of ``kind`` fills in beside it, for ``user``.

    Only text the person's own records hold, only non-empty, and nothing for a kind with no
    ``fills`` or one that is not usable by them. A failing ``fills`` is an empty answer: a
    fill is a convenience, never a reason for the form to break.
    """
    suggester = _registry.get(kind)
    if suggester is None or suggester.fills is None or not usable(suggester, user):
        return {}
    try:
        found = suggester.fills(user, (chosen or "")[:TYPED_AT_MOST])
    except Exception:
        return {}
    return {str(key): str(text) for key, text in found.items() if str(text).strip()}


class SuggestInput(forms.TextInput):
    """A text box that suggests as it is typed in: the markup the script looks for.

    ``data-suggest`` is the address of the answers. A ``list`` attribute may stay beside it:
    a browser with scripts off offers that `<datalist>` natively and the script takes the
    attribute off when it draws its own list, so one box never shows two.
    """

    def __init__(
        self,
        kind: str,
        attrs: dict | None = None,
        *,
        fills: dict[str, str] | None = None,
    ):
        """``fills`` maps what the kind can fill (a key of its ``fills``) to the name of the
        field beside this one that takes it; the script fills such a field only while it is
        empty, says so, and offers to undo it."""
        own = {
            "data-suggest": reverse_lazy("core:suggest", args=[kind]),
            "autocomplete": "off",
            **(
                {
                    "data-suggest-fills": json.dumps(fills),
                    "data-suggest-fill-address": reverse_lazy("core:suggest_fill", args=[kind]),
                    # Said by the script, so they are the page's own words and language.
                    "data-fill-said": _("Also filled in:"),
                    "data-fill-undo": _("Undo"),
                    "data-fill-undone": _("Undone."),
                }
                if fills
                else {}
            ),
            **(attrs or {}),
        }
        super().__init__(own)
