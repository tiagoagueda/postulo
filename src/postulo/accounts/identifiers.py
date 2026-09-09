"""The external identifiers a person can carry — the registry, seen from the person's side.

#42 gave companies external identifiers because a name is not an identity. The same is true
of a person, and in the places Postulo is aimed at first — academic posts, research
institutes, EU bodies — the identifier somebody actually has is an **ORCID**: sixteen digits
saying which researcher this is, regardless of how their name is spelled, married,
transliterated or abbreviated on any given day.

It belongs on a CV. It is also the identifier an application form asks for by name, and
having it in the record means never looking it up again.

**This module used to hold a second copy of the machinery, and a second table of schemes.**
Both are gone: there is one registry now, in `postulo.plugins.identifiers`, and each scheme
says which subjects it identifies. What is left here is the person's view of it — which is
where the guarantee lives, because *every* function below is scoped to `PERSON` and cannot
return a company's scheme however it is called (#109).

Three schemes that were companies-only or people-only turned out to be both. A researcher's
Wikidata item and their LinkedIn profile are now recordable, and were not before.

**Nothing here asks ORCID whether an identifier exists.** Postulo makes no request nobody
asked for, and the checksum catches the typos that a lookup would — which is the entire
reason ORCID has one.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from postulo.core import identifiers as registry
from postulo.core.identifiers import PERSON

#: The keys, re-exported so callers need not know which module the table lives in. They are
#: the same strings they always were; every stored row carries one.
ORCID = "orcid"
RESEARCHERID = "researcherid"
SCOPUS = "scopus"
ISNI = "isni"
WIKIDATA = "wikidata"
LINKEDIN = "linkedin"
OTHER = "other"


def schemes() -> dict:
    """Every scheme that identifies a person."""
    return registry.schemes_for(PERSON)


def choices() -> list[tuple[str, object]]:
    return [(key, scheme.label) for key, scheme in schemes().items()]


def scheme_for(key: str):
    """One of them, or nothing — including when the key is a company's."""
    return registry.find(key, PERSON)


def label_for(key: str) -> str:
    return registry.label_for(key, PERSON)


def normalise(scheme_key: str, raw: str) -> str:
    """Tidy ``raw`` into the canonical spelling for its scheme."""
    scheme = scheme_for(scheme_key)
    return scheme.normalise(raw) if scheme else (raw or "").strip()


def validate(scheme_key: str, value: str) -> None:
    """Raise :class:`ValidationError` unless ``value`` is a well-formed identifier.

    A scheme that does not identify people is *unknown* here rather than merely wrong,
    which is the point: an LEI reaching this is an LEI on a person, and the honest answer
    is that there is no such scheme for a person.
    """
    scheme = scheme_for(scheme_key)
    if scheme is None:
        raise ValidationError(_("Unknown identifier scheme."), code="scheme")
    if not scheme.pattern.match(value):
        raise ValidationError(
            _("That does not look like a %(scheme)s identifier (for example %(example)s)."),
            code="format",
            params={"scheme": scheme.label, "example": scheme.example},
        )
    if scheme.checksum is not None and not scheme.checksum(value):
        raise ValidationError(scheme.checksum_message, code="checksum")


def clean(scheme_key: str, raw: str) -> str:
    """Normalise then validate, returning the canonical value."""
    if scheme_for(scheme_key) is None:
        raise ValidationError(_("Unknown identifier scheme."), code="scheme")
    value = normalise(scheme_key, raw)
    validate(scheme_key, value)
    return value


def url_for(scheme_key: str, value: str) -> str:
    scheme = scheme_for(scheme_key)
    return scheme.url_for(value, PERSON) if scheme else ""
