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

from django.utils.text import get_text_list

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


def set_primary(profile, row) -> None:
    """Make ``row`` the profile's primary identifier, and the only one (#922).

    Cleared first and set second: the partial unique index means two of them cannot exist
    even for the length of a statement. Written with ``update`` so that nothing but the flag
    is touched.
    """
    from .models import PersonIdentifier

    PersonIdentifier.objects.filter(profile=profile, is_primary=True).exclude(pk=row.pk).update(
        is_primary=False
    )
    PersonIdentifier.objects.filter(pk=row.pk).update(is_primary=True)
    row.is_primary = True


def ensure_one_primary(profile):
    """The profile keeps the primary identifier it has; failing that, its first becomes it.

    What saving the rows does when nobody said which is the primary, and what taking the
    primary off does, so that the two ways of losing it cannot disagree about who inherits
    it. Nothing when the profile holds no identifier.
    """
    from .models import PersonIdentifier

    rows = PersonIdentifier.objects.filter(profile=profile)
    wanted = rows.filter(is_primary=True).first() or rows.first()
    if wanted is not None and not wanted.is_primary:
        set_primary(profile, wanted)
    return wanted


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


def brand_for(key: str) -> str:
    """The brand mark Postulo ships for the scheme, or nothing (#654)."""
    return registry.brand_for(key, PERSON)


def normalise(scheme_key: str, raw: str) -> str:
    """Tidy ``raw`` into the canonical spelling for its scheme."""
    return registry.normalise(PERSON, scheme_key, raw)


def validate(scheme_key: str, value: str) -> None:
    """Raise :class:`ValidationError` unless ``value`` is a well-formed identifier.

    A scheme that does not identify this subject is *unknown* here rather than merely
    wrong; the registry says so, once, for both (#645).
    """
    registry.validate(PERSON, scheme_key, value)


def clean(scheme_key: str, raw: str) -> str:
    """Normalise then validate, returning the canonical value."""
    return registry.clean(PERSON, scheme_key, raw)


def pasted_whole() -> str:
    """The registers whose address can be pasted whole, as the reader's language writes a
    list: "ORCID, Wikidata or LinkedIn" (#302).

    The schemes that lift an identifier out of an address on their own host, read from the
    registry, so what *Your details* says may be pasted is what is lifted: a sentence that
    said "paste the whole address" of every kind was untrue of a ResearcherID.
    """
    return get_text_list([str(scheme.label) for scheme in schemes().values() if scheme.url_paths])


def url_for(scheme_key: str, value: str) -> str:
    return registry.url_for(PERSON, scheme_key, value)
