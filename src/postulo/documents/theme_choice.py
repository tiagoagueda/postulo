"""Which themes a person is offered for one document, the suitable ones first (#1255).

The vocabulary is `themes`, a registry of plain values the records read; this is the part
that reads a person's records -- their primary address -- and so sits above them, as
`paper_choice` sits above `paper`.
"""

from __future__ import annotations

from postulo.core import languages
from postulo.core.postal import primary_for

from .themes import choices_for, for_kind


def document_country(owner, language: str = "") -> str:
    """The country a document is for, as far as Postulo can tell.

    The region of its language tag where it names one -- a letter in ``fr-CA`` is for
    Canada -- else the country of the person's primary address, else nothing. A region that
    is no country (``es-419``, Latin America) is passed over.
    """
    region = languages.region_of(language)
    if region.isalpha():
        return region
    profile = getattr(owner, "profile", None) if owner is not None else None
    address = primary_for(profile) if profile is not None else None
    return (address.country or "").upper() if address is not None else ""


def offered_for(kind: str, owner, language: str = "") -> list:
    """`themes.choices_for` for a document in ``language`` that belongs to ``owner``.

    The person's address is read only where some theme says what it suits.
    """
    if not any(one.declares_where() for one in for_kind(kind)):
        return choices_for(kind)
    return choices_for(kind, language=language, country=document_country(owner, language))
