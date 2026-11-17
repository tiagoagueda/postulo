"""The placeholder checks: the first writing checker, and the one that is always on (#1258).

A letter's placeholders were checked by one function, `documents.rendering.unfilled_placeholders`,
and its answer was one sentence on the page that freezes a letter (#235). The check is a
`writing-checker` now, so that what it finds and what any other checker finds are **one list**
and not two, drawn by the same component on the letter's page and before a letter is frozen.

Two checks, both certain and both about what an employer would see:

- ``placeholder-empty``: a placeholder Postulo fills in, with nothing to fill it for the
  application chosen. It goes out blank. Said once for each name, where it is first used.
  With no application chosen the four it would fill are not asked about at all, so a
  template is not told that its company is missing.
- ``placeholder-unknown``: ``{{ compnay }}`` is not a placeholder, so it goes out exactly as
  typed. Where one of the real names is close, that name is proposed.

**Required** (#877): no switch moves it, exactly as the check was before it was a plugin. It
reads nothing but the document it is handed, so it imports the plugin surface and nothing else.
"""

from __future__ import annotations

from difflib import get_close_matches

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import WritingChecker, WritingFinding, declares, shipped

#: The identifier the registry and the policy key on.
PLACEHOLDER_CHECKS = "placeholder-checks"

#: How much of a name a sentence quotes: a sentence is one line of at most 300 characters.
NAME_SHOWN = 60

#: How alike a misspelt name has to be to one of the real ones before that one is proposed.
CLOSE_ENOUGH = 0.6


def check(document):
    """A finding for each placeholder that will go out blank, and each that is no placeholder."""
    said: set[str] = set()
    for passage in document.passages:
        for held in passage.placeholders:
            if not held.known:
                near = get_close_matches(
                    held.name.lower(), document.placeholder_names, n=1, cutoff=CLOSE_ENOUGH
                )
                yield WritingFinding(
                    check="placeholder-unknown",
                    message=gettext("{{ %(name)s }} is not a placeholder, so it goes out as typed.")
                    % {"name": held.name[:NAME_SHOWN]},
                    passage=passage.key,
                    start=held.start,
                    end=held.end,
                    replacements=tuple("{{ " + name + " }}" for name in near),
                    severity="problem",
                )
            elif held.value == "" and held.name not in said:
                said.add(held.name)
                yield WritingFinding(
                    check="placeholder-empty",
                    message=gettext("Nothing fills in {{ %(name)s }}, so it goes out blank.")
                    % {"name": held.name[:NAME_SHOWN]},
                    passage=passage.key,
                    start=held.start,
                    end=held.end,
                    severity="problem",
                )


@declares(
    shipped(
        name=PLACEHOLDER_CHECKS,
        label=_("Placeholder checks"),
        kind="writing-checker",
        description=_(
            "Says, before a letter goes, which placeholders have nothing to fill them and "
            "would go out blank, and which are misspelt and would go out as typed. It reads "
            "the letter as it is stored and asks no service. Always on."
        ),
        icon="braces",
        required=True,
    )
)
class PlaceholderChecks:
    """A reader of letters. Postulo hands it a document; it changes nothing and asks nobody."""

    checkers = (
        WritingChecker(
            key="placeholder-checks",
            label=_("Placeholder checks"),
            documents=("letter", "message"),
            languages=(),
            offline=True,
            check=check,
        ),
    )
