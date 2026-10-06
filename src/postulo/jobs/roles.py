"""The roles a company can play, and the NACE divisions that qualify a company for each (#671).

A company has no classification of its own: its **industries** carry one, because an
`Industry` whose name is a NACE division's name has that division's code (`industries.py`).
A *role* is a question asked of that: "does this company do what an intermediary does?",
answered from the codes of its industries and never from a column of its own, so nothing
new is stored and no archive, API or candidate format changes.

**Three roles ship**: the intermediary, a company that stands between a listing and its
employer -- a recruitment agency, a staffing firm, a placement service -- which is NACE
division 78, *Employment activities*; the place of learning (#685), the institution an
education entry names, which is division 85, *Education*; and the awarding body (#686), who
issues a certification: education and testing bodies (85) and professional membership
organisations (94). In Rev. 2.1 division 85 sits in section Q and holds universities (85.40),
schools (85.10 to 85.33) and non-formal training (85.5) alike, so the whole division is taken
and no class is needed. A research institute (72.10, 72.20) a person names as the place of a
doctorate is not in 85 and is left out on purpose: the role's set is data, and one more
division here adds it. A vendor that certifies its own users is classified by what it sells,
so the awarding body's set only puts the likely issuers first and turns nothing away. The
membership organisation (94, #693) is the fourth, and an honour's giver is offered as an
awarding body: universities, foundations, firms and governments all award things, so the role
orders the list and restricts nothing. Each adds a row to `ROLES` and nothing else. An
*employer* is not a role: any company may be one.

**A kind can qualify on its own.** The public employment service (`CompanyKind`) is the
office a person is registered with, and it refers people to other companies' listings. It is
not certain that NACE puts a public body in division 78, so the kind qualifies whatever
industries it has, and the answer does not depend on the classification's wording.

The role *offers* and does not *require*: callers put the qualifying companies first and keep
the rest one step away, because no existing company carries a code yet, and a row that names
an agency which does not qualify must not become invalid.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Role:
    """One thing a company can be to the person recording it, and what makes it so."""

    key: str
    label: object  # a lazy string
    #: The NACE divisions whose industries qualify a company.
    divisions: frozenset[str]
    #: Company kinds (`jobs.CompanyKind` values) that qualify on their own, written as the
    #: stored strings so that this module imports no model.
    kinds: frozenset[str] = frozenset()


INTERMEDIARY = "intermediary"
PLACE_OF_LEARNING = "place_of_learning"
AWARDING_BODY = "awarding_body"
MEMBERSHIP_ORGANISATION = "membership_organisation"

ROLES: dict[str, Role] = {
    INTERMEDIARY: Role(
        key=INTERMEDIARY,
        label=_("Employment services and agencies"),
        divisions=frozenset({"78"}),
        kinds=frozenset({"employment_service"}),
    ),
    PLACE_OF_LEARNING: Role(
        key=PLACE_OF_LEARNING,
        label=_("Places of learning"),
        divisions=frozenset({"85"}),
    ),
    AWARDING_BODY: Role(
        key=AWARDING_BODY,
        label=_("Awarding bodies"),
        divisions=frozenset({"85", "94"}),
    ),
    # NACE division 94, *Activities of membership organisations*, offered first for a
    # membership and never required: an association named for a sport or a trade may sit in
    # another division (#693).
    MEMBERSHIP_ORGANISATION: Role(
        key=MEMBERSHIP_ORGANISATION,
        label=_("Membership organisations"),
        divisions=frozenset({"94"}),
    ),
}


def role(key: str) -> Role:
    """The role with this key. An unknown key is refused rather than answered with nothing,
    because a typo that quietly qualified no company would look like an empty list."""
    try:
        return ROLES[key]
    except KeyError:
        raise ValueError(f"unknown company role: {key!r}") from None
