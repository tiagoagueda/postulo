"""Rules that compare one identifier of a company with another (#675).

Every scheme is checked alone, by shape and by check digit where it has one. These compare.
There are two, and they are small on purpose: what can be said **offline** about a company's
identifiers is little. A LEI carries no country (its first four characters are its issuer's
prefix), and nothing on a company says what its ISNI should be, so *a valid Wikidata item with
somebody else's ISNI* is not here -- it needs a record to compare with, and that is a lookup,
which is a deliberate act and not something a page does by itself.

**A finding tells and never refuses.** It is worked out when the page is drawn and nothing is
kept, so there is no table to go stale. A register may be right where another record is
wrong, and a company may be registered in more than one place.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from postulo.core import identifiers as registry
from postulo.core.identifiers import COMPANY, Finding

from .schemes import OPENCORPORATES, REGISTER

#: Where a register and OpenCorporates spell one country two ways: Greece is EL in a
#: register and gr at OpenCorporates, and the United Kingdom is gb at the latter.
_COUNTRY_ALIASES = {"EL": "GR", "UK": "GB"}


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _country(code: str) -> str:
    code = code.upper()
    return _COUNTRY_ALIASES.get(code, code)


def no_longer_valid(subject: str, rows: list) -> list[Finding]:
    """Stored values that today's rules for their own scheme would refuse.

    What a scheme accepts changes between releases, and a row saved earlier stays as it was
    until its form is next saved. An unknown scheme is left to the row's own marking.
    """
    found = []
    for row in rows:
        scheme = registry.find(row.scheme, subject)
        if scheme is None:
            continue
        try:
            registry.refuse_malformed(scheme, scheme.normalise(row.value))
        except ValidationError as error:
            found.append(
                Finding(
                    _("%(scheme)s %(value)s would be refused today: %(reason)s")
                    % {
                        "scheme": scheme.label,
                        "value": row.value,
                        "reason": " ".join(error.messages),
                    },
                    ((str(scheme.label), row.value),),
                )
            )
    return found


def register_against_opencorporates(subject: str, rows: list) -> list[Finding]:
    """A register number and an OpenCorporates id that name different companies."""
    if subject != COMPANY:
        return []
    register = next((row for row in rows if row.scheme == REGISTER), None)
    corporates = next((row for row in rows if row.scheme == OPENCORPORATES), None)
    if register is None or corporates is None:
        return []
    register_country, _sep, register_number = register.value.partition(" ")
    jurisdiction, _sep, corporates_number = corporates.value.partition("/")
    involved = (
        (str(registry.label_for(REGISTER, COMPANY)), register.value),
        (str(registry.label_for(OPENCORPORATES, COMPANY)), corporates.value),
    )
    params = {"register": register.value, "corporates": corporates.value}
    if _country(register_country) != _country(jurisdiction[:2]):
        return [
            Finding(
                _(
                    "The register number %(register)s and the OpenCorporates id "
                    "%(corporates)s name different jurisdictions. That can be right for "
                    "a company registered in more than one."
                )
                % params,
                involved,
            )
        ]
    if _alnum(register_number) != _alnum(corporates_number):
        return [
            Finding(
                _(
                    "The register number %(register)s and the OpenCorporates id "
                    "%(corporates)s name the same jurisdiction but different numbers."
                )
                % params,
                involved,
            )
        ]
    return []


#: Every rule this plugin brings, in the order their findings are listed.
CROSS_CHECKS = (no_longer_valid, register_against_opencorporates)
