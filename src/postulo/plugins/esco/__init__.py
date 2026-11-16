"""ESCO: the European classification of occupations and skills, as a source of suggestions (#1252).

The second reference vocabulary behind the `suggestion-source` kind, after NACE. It suggests
for the ``job_title`` field the ISCO-08 unit groups ESCO places occupations in, in the
reader's language, and it answers the questions the rest of Postulo asks of occupations and
skills -- which unit group a title is, which skill a name is -- through
`postulo.core.occupations`, which asks the registry and degrades when this plugin is off.

**Bundled and on by default**, because it reads files and asks nobody anything at run time.
The files are downloaded once by ``manage.py fetch_esco`` (this plugin's command, the one
request it makes and only when an administrator runs it) into `POSTULO_ESCO_DIR`; without them
a title matches nothing and the title box offers nothing, which is the ordinary state of a
title that matches nothing. Their licence note stays beside the other downloaded data
(`jobs/data/ESCO-LICENCE.md`).

**Switched off, nothing is deleted.** The unit groups on postings and the skill identifiers
on a career record stay as they are, as plain data, and are neither shown nor worked out
again until it is switched on.

The reader is `classification`; it is loaded when it is first asked something, not when the
plugin is registered.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import SuggestionSource, declares, shipped

#: The identifier the registry keys on.
ESCO = "esco"

ESCO_LICENCE = "EUPL 1.2 (European Commission, ESCO)"


def _offer(field, exclude=(), language="", first=()):
    """The unit groups of the classification for a job title; ``first`` are NACE divisions
    whose unit groups lead, and the rest follows (#897)."""
    from . import classification

    if field != "job_title":
        return []
    names = classification.suggestions(exclude=exclude, language=language, sections=first)
    return [(name, "") for name in names]


@declares(
    shipped(
        name=ESCO,
        label=_("ESCO occupations and skills"),
        kind="suggestion-source",
        description=_(
            "Suggests job titles from ESCO, the European classification of occupations, "
            "in your language, and recognises the skills you record against ESCO's list of "
            "skills. It reads files an administrator downloads once with manage.py "
            "fetch_esco and asks no service while you work. Switched off, the title and "
            "skill boxes are plain and every code and skill already kept stays as it is."
        ),
    )
)
class Esco:
    """A source of suggestions, and the reader of the classification behind them."""

    sources = (
        SuggestionSource(
            key="esco",
            label="ESCO",
            fields=("job_title",),
            languages=(),
            offline=True,
            licence=ESCO_LICENCE,
            offer=_offer,
        ),
    )

    @property
    def classification(self):
        """The module that reads the downloaded files; `postulo.core.occupations` asks it."""
        from . import classification

        return classification
