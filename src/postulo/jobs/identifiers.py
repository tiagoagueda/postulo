"""The external identifiers a company can carry, and what each one knows.

A company in Postulo is a name in one person's account. An identifier ties that name to a
public record — a Wikidata item, a legal-entity identifier, a national register number, a
profile on a professional network — so two accounts, an importer or a plugin can say
"the same employer" without comparing spellings.

Each scheme knows three things: how to tidy what someone pasted (a whole Wikidata URL,
a lower-case ``q42``, a LinkedIn address with a trailing slash), whether the result is
well-formed, and where it links. Nothing here touches the network; the person typing an
identifier knows what they typed, and looking it up is a deliberate action for later.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from postulo.core.identifiers import Scheme, value_from_url

# Registered in the order the form offers them: the ones people actually have to hand
# come first.
WIKIDATA = "wikidata"
LEI = "lei"
REGISTER = "register"
LINKEDIN = "linkedin"
CRUNCHBASE = "crunchbase"
OPENCORPORATES = "opencorporates"
OTHER = "other"


SCHEMES: dict[str, Scheme] = {
    scheme.key: scheme
    for scheme in (
        Scheme(
            WIKIDATA,
            "Wikidata",
            re.compile(r"^Q[1-9]\d{0,11}$"),
            link="https://www.wikidata.org/wiki/{value}",
            example="Q95",
            url_paths=("/wiki/", "/entity/"),
            hosts=("wikidata.org",),
            upper=True,
        ),
        Scheme(
            LEI,
            _("Legal Entity Identifier (LEI)"),
            re.compile(r"^[A-Z0-9]{18}\d{2}$"),
            link="https://search.gleif.org/#/record/{value}",
            example="HWUPKR0MPOU8FGXBT394",
            url_paths=("/record/",),
            hosts=("gleif.org",),
            upper=True,
        ),
        Scheme(
            REGISTER,
            _("Company register number"),
            # A two-letter country, then the number as the register writes it.
            re.compile(r"^[A-Z]{2} [A-Z0-9][A-Z0-9 .\-/]{1,38}$"),
            example=_("PT 501234567, FR 552081317, DE HRB 12345"),
            upper=True,
        ),
        Scheme(
            LINKEDIN,
            "LinkedIn",
            re.compile(r"^[a-z0-9][a-z0-9._\-]{0,99}$"),
            link="https://www.linkedin.com/company/{value}/",
            example="aperture-science",
            url_paths=("/company/", "/school/", "/showcase/"),
            hosts=("linkedin.com",),
        ),
        Scheme(
            CRUNCHBASE,
            "Crunchbase",
            re.compile(r"^[a-z0-9][a-z0-9._\-]{0,99}$"),
            link="https://www.crunchbase.com/organization/{value}",
            example="aperture-science",
            url_paths=("/organization/",),
            hosts=("crunchbase.com",),
        ),
        Scheme(
            OPENCORPORATES,
            "OpenCorporates",
            # jurisdiction code, a slash, the number: gb/01234567 or us_de/2345678
            re.compile(r"^[a-z]{2}(_[a-z]{2,3})?/[A-Za-z0-9.\-]{1,40}$"),
            link="https://opencorporates.com/companies/{value}",
            example="gb/01234567",
            url_paths=("/companies/",),
            hosts=("opencorporates.com",),
        ),
        Scheme(
            OTHER,
            _("Other"),
            re.compile(r"^\S(.{0,98}\S)?$"),
            example=_("Any identifier, with a name for it"),
        ),
    )
}

CHOICES = tuple((scheme.key, scheme.label) for scheme in SCHEMES.values())
LINKED = tuple(key for key, scheme in SCHEMES.items() if scheme.link)


def normalise(scheme_key: str, raw: str) -> str:
    """Tidy ``raw`` into the canonical spelling for its scheme.

    Pasting is the common case: the value is lifted out of a URL when the address is one
    of the scheme's, whitespace is trimmed, case is folded where the scheme is
    case-insensitive. Register numbers keep the country apart from the number with one
    space so ``PT501234567`` and ``PT 501 234 567`` read the same.
    """
    scheme = SCHEMES[scheme_key]
    value = (raw or "").strip()
    # An OpenCorporates company keeps its slash: /companies/gb/01234567
    lifted = value_from_url(value, scheme, segments=2 if scheme_key == OPENCORPORATES else 1)
    if lifted is not None:
        value = lifted
    if scheme_key in (LINKEDIN, CRUNCHBASE, OPENCORPORATES):
        value = value.lower()
    if scheme.upper:
        value = value.upper()
    if scheme_key == LEI:
        value = value.replace(" ", "")
    if scheme_key == REGISTER:
        value = re.sub(r"\s+", " ", value)
        if len(value) > 2 and value[:2].isalpha() and value[2] != " ":
            value = value[:2] + " " + value[2:].lstrip(" -:")
    return value


def _lei_checks_out(value: str) -> bool:
    """ISO 7064 MOD 97-10: letters become two digits, and the whole thing is 1 mod 97."""
    digits = "".join(str(int(char, 36)) for char in value)
    return int(digits) % 97 == 1


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
    if scheme_key == LEI and not _lei_checks_out(value):
        raise ValidationError(_("The LEI's check digits do not match."), code="checksum")


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
