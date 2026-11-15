"""NACE: the classification of economic activities, as a source of industry suggestions (#1252).

The first reference vocabulary to sit behind the `suggestion-source` kind. It suggests for the
``industry`` field the short names Postulo has always offered and the divisions of NACE
Rev. 2.1 in the reader's language (`postulo.jobs.industries`, whose data and licence are
`jobs/data/nace-2.1.json` and `LICENCE.md`), each with the sector it sits in.

**Bundled and on by default**, because it reads files that ship with Postulo and asks nobody
anything. An administrator removes it by disabling the plugin, and the field is then a plain
input: the industries and sectors already on a company stay exactly as they are, as plain
data.

This slice moves only *who is asked*. The classification module stays in `jobs`, where
models, a migration, the reports and the importer still read it directly; moving those
readers behind the registry is what the later slices are for.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import SuggestionSource, declares, shipped

#: The identifier the registry keys on. Not a policy key -- nothing decides about it.
NACE = "nace"

NACE_LICENCE = "CC BY 4.0 (Eurostat, NACE Rev. 2.1)"


def _offer(field, exclude=(), language="", first=()):
    """Industry names for the input: Postulo's short names, then the NACE divisions."""
    from postulo.jobs import industries

    if field != "industry":
        return []
    return industries.offered(exclude=exclude, language=language, first=first)


@declares(
    shipped(
        name=NACE,
        label=_("NACE sectors and industries"),
        kind="suggestion-source",
        description=_(
            "Suggests industries from the short names Postulo offers and from NACE, the "
            "European classification of economic activities, in your language where "
            "Eurostat published it, each under its sector. It reads files that ship with "
            "Postulo and asks no service. Switched off, the industry field is a plain box "
            "and every industry already kept stays as it is."
        ),
    )
)
class Nace:
    """A source of suggestions. Postulo asks it what to offer; it never asks anything of anyone."""

    sources = (
        SuggestionSource(
            key="nace",
            label="NACE",
            fields=("industry",),
            languages=(),
            offline=True,
            licence=NACE_LICENCE,
            offer=_offer,
        ),
    )
