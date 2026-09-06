"""The external identifiers a person can carry, starting with ORCID.

#42 gave companies external identifiers because a name is not an identity. The same is
true of a person, and in the places Postulo is aimed at first — academic posts, research
institutes, EU bodies — the identifier somebody actually has is an **ORCID**: sixteen
digits saying which researcher this is, regardless of how their name is spelled, married,
transliterated or abbreviated on any given day.

It belongs on a CV. It is also the identifier an application form asks for by name, and
having it in the record means never looking it up again.

The machinery is the one companies use (`postulo.core.identifiers`); only the schemes
differ, because the things being identified do.

**Nothing here asks ORCID whether an identifier exists.** Postulo makes no request nobody
asked for, and the checksum catches the typos that a lookup would — which is the entire
reason ORCID has one.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from postulo.core.identifiers import Scheme, value_from_url

ORCID = "orcid"
RESEARCHERID = "researcherid"
SCOPUS = "scopus"
ISNI = "isni"
OTHER = "other"

SCHEMES: dict[str, Scheme] = {
    scheme.key: scheme
    for scheme in (
        Scheme(
            ORCID,
            "ORCID",
            pattern=re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"),
            link="https://orcid.org/{value}",
            example="0000-0002-1825-0097",
            url_paths=("/",),
            hosts=("orcid.org",),
            upper=True,
        ),
        Scheme(
            RESEARCHERID,
            "ResearcherID",
            pattern=re.compile(r"^[A-Z]-\d{4}-\d{4}$"),
            link="https://www.webofscience.com/wos/author/record/{value}",
            example="A-1234-2020",
            upper=True,
        ),
        Scheme(
            SCOPUS,
            "Scopus Author ID",
            pattern=re.compile(r"^\d{10,11}$"),
            link="https://www.scopus.com/authid/detail.uri?authorId={value}",
            example="7004212771",
        ),
        Scheme(
            ISNI,
            "ISNI",
            pattern=re.compile(r"^\d{4} \d{4} \d{4} \d{3}[\dX]$"),
            link="https://isni.org/isni/{value}",
            example="0000 0001 2281 955X",
            upper=True,
        ),
        Scheme(
            OTHER,
            _("Other"),
            pattern=re.compile(r"^\S.{0,98}\S$|^\S$"),
            example=_("a staff number, a national registration"),
        ),
    )
}

CHOICES = [(key, scheme.label) for key, scheme in SCHEMES.items()]


def _orcid_checks_out(value: str) -> bool:
    """ISO 7064 MOD 11-2, which is what the last character of an ORCID is.

    An ORCID that fails its own checksum is a typo, every time. Checking it here is why
    Postulo never has to ask orcid.org whether an identifier is real.
    """
    digits = value.replace("-", "")
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    remainder = total % 11
    expected = (12 - remainder) % 11
    return ("X" if expected == 10 else str(expected)) == digits[-1]


def normalise(scheme_key: str, raw: str) -> str:
    """Tidy ``raw`` into the canonical spelling for its scheme.

    Pasting is the common case, and for an ORCID it is usually the whole address. The
    digits are also written every way a person might type them — with spaces, with no
    separators at all — so they are put back into groups of four.
    """
    scheme = SCHEMES[scheme_key]
    value = (raw or "").strip()

    lifted = value_from_url(value, scheme)
    if lifted is not None:
        value = lifted

    if scheme.upper:
        value = value.upper()

    if scheme_key == ORCID:
        digits = re.sub(r"[^0-9X]", "", value)
        if len(digits) == 16:
            value = "-".join(digits[i : i + 4] for i in range(0, 16, 4))
    elif scheme_key == ISNI:
        digits = re.sub(r"[^0-9X]", "", value)
        if len(digits) == 16:
            value = " ".join(digits[i : i + 4] for i in range(0, 16, 4))
    elif scheme_key == SCOPUS:
        value = re.sub(r"\D", "", value)
    return value


def validate(scheme_key: str, value: str) -> None:
    """Raise :class:`ValidationError` unless ``value`` is a well-formed identifier."""
    scheme = SCHEMES.get(scheme_key)
    if scheme is None:
        raise ValidationError(_("Unknown identifier scheme."), code="scheme")
    if not scheme.pattern.match(value):
        raise ValidationError(
            _("That does not look like a %(scheme)s identifier (for example %(example)s)."),
            code="format",
            params={"scheme": scheme.label, "example": scheme.example},
        )
    if scheme_key == ORCID and not _orcid_checks_out(value):
        raise ValidationError(
            _("That ORCID's last digit does not match the rest, so one of them is a typo."),
            code="checksum",
        )


def clean(scheme_key: str, raw: str) -> str:
    """Normalise then validate, returning the canonical value."""
    if scheme_key not in SCHEMES:
        raise ValidationError(_("Unknown identifier scheme."), code="scheme")
    value = normalise(scheme_key, raw)
    validate(scheme_key, value)
    return value


def url_for(scheme_key: str, value: str) -> str:
    scheme = SCHEMES.get(scheme_key)
    return scheme.url_for(value) if scheme else ""
