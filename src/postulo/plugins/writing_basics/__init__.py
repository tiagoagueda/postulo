"""Writing basics: a local writing checker for letters, and the reference one (#1258).

Five sorts of hint, each its own sub-plugin so that somebody who writes long sentences on
purpose loses that one and keeps the rest:

- **names**: with an application chosen, the letter names neither the company (by
  ``{{ company }}`` or any name it goes by) nor the role. Not of a follow-up note.
- **starter notes**: a note in square brackets, as the starters leave them, still in it.
- **length**: the body against what its kind of letter usually keeps to on a page.
- **repetition**: the same word twice in a row, and several spaces where one would do; each
  with the replacement, which is put in only when the person presses for it.
- **sentences**: a sentence of more than forty words.

**No dictionary, no network, no dependency, no model**: a few regular expressions over the
text the person stored (`checks`). It asks nobody anything and keeps nothing.

**Bundled and off until an administrator switches it on** (`plugins` migration
``0020_writing_basics_starts_off``), because style hints are opinions, where the placeholder
checks are facts. A bundled plugin is the administrator's to switch, never the person's; the
hints fold away under their own heading. Spelling is deliberately not here: the browser
checks it already, in the letter's language, and a dictionary small, clearly licensed and
good in the languages Postulo speaks was not found.

It imports the plugin surface and nothing else, and is written as a package outside Postulo
would write one.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import SubPlugin, WritingChecker, declares, shipped

from . import checks

#: The identifier the registry and the policy key on.
WRITING_BASICS = "writing-basics"

#: Each sort of hint: its sub-plugin's id, what it is called, what it does, and the check.
SORTS = (
    (
        "names",
        _("A letter that names nobody"),
        _(
            "With an application chosen, says when the letter names neither the company nor "
            "the role. Not for a follow-up note."
        ),
        checks.names,
    ),
    (
        "starter-notes",
        _("Notes left from the starter"),
        _("Says when a note in square brackets, as the starter text writes them, is still in."),
        checks.starter_notes,
    ),
    (
        "length",
        _("Length"),
        _(
            "Says when the letter is longer than its kind usually keeps to on a page: about "
            "400 words for a cover or speculative letter, 150 for a follow-up note, 800 for a "
            "motivation letter."
        ),
        checks.length,
    ),
    (
        "repetition",
        _("Repeated words and spaces"),
        _(
            "Says when the same word is written twice in a row, or several spaces stand "
            "where one would do, and offers to put it right."
        ),
        checks.repetition,
    ),
    (
        "sentences",
        _("Long sentences"),
        _("Says when a sentence runs past forty words."),
        checks.sentences,
    ),
)


@declares(
    shipped(
        name=WRITING_BASICS,
        label=_("Writing basics"),
        kind="writing-checker",
        description=_(
            "Hints about a letter before it goes: one that names neither the company nor "
            "the role, a note left from the starter, its length for its kind, a word written "
            "twice, several spaces, a very long sentence. It reads the letter as it is "
            "stored, asks no service and keeps nothing. Off until an administrator switches "
            "it on."
        ),
        icon="pencil",
    )
)
class WritingBasics:
    """A reader of letters. Postulo hands it a document; it changes nothing and asks nobody."""

    sub_plugins = tuple(
        SubPlugin(id=key, label=label, description=description)
        for key, label, description, _check in SORTS
    )

    checkers = tuple(
        WritingChecker(
            key=f"{WRITING_BASICS}-{key}",
            label=label,
            documents=("letter",),
            languages=(),
            offline=True,
            check=check,
            sub_plugin=key,
        )
        for key, label, _description, check in SORTS
    )
