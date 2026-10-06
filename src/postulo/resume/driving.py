"""The categories of a driving licence, as codes (#691).

Directive 2006/126/EC, Article 4, defines fifteen categories, and Directive (EU) 2025/2205,
Article 6(1), which repeals it from 26 November 2029, keeps the same fifteen. This is the one
place that lists them: a sixteenth, if one ever comes, is a row here and nowhere else.

**A code is a code.** It is stored as the letters the licence prints, in the Directive's
order, and a CV prints the letters. The sentence beside each is Postulo's own help text for
somebody who does not know what a C1 is, and no CV ever carries it. No category implies
another -- a person who holds A ticks A -- because what a CV says is what the person ticked.

**What is never kept** is stated in `docs/THREAT-MODEL.md`: the licence number, the
photograph, the signature, the residence, and the restriction codes of the Union model's
field 12, which are data concerning health where they say a driver wears glasses.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: In the Directive's order, which is the order a licence prints them and a CV does.
CATEGORIES: tuple[tuple[str, object], ...] = (
    ("AM", _("mopeds and light quadricycles")),
    ("A1", _("light motorcycles, up to 125 cc")),
    ("A2", _("medium-power motorcycles, up to 35 kW")),
    ("A", _("motorcycles of any power")),
    ("B1", _("light four-wheeled vehicles (quadricycles)")),
    ("B", _("cars and light vans")),
    ("BE", _("a car or light van with a heavier trailer")),
    ("C1", _("medium-sized lorries")),
    ("C1E", _("a medium-sized lorry with a trailer")),
    ("C", _("lorries")),
    ("CE", _("a lorry with a trailer")),
    ("D1", _("minibuses")),
    ("D1E", _("a minibus with a trailer")),
    ("D", _("buses")),
    ("DE", _("a bus with a trailer")),
)

CODES: tuple[str, ...] = tuple(code for code, _summary in CATEGORIES)

#: The longest a note of national letters or classes may be.
MAX_OTHER = 100


def choices() -> list[tuple[str, str]]:
    """Each category as a form choice: the code, and the code with its summary."""
    return [(code, f"{code} — {summary}") for code, summary in CATEGORIES]


def ordered(values) -> list[str]:
    """The codes among ``values``, once each and in the Directive's order.

    Whatever is not a code is dropped; `validate` is what says so.
    """
    held = {value for value in values if isinstance(value, str)}
    return [code for code in CODES if code in held]


def validate(value) -> None:
    """Refuse anything that is not a list of codes from the table."""
    if not isinstance(value, list):
        raise ValidationError(_("Categories are a list of codes."), code="invalid")
    for item in value:
        if not isinstance(item, str) or item not in CODES:
            raise ValidationError(
                _("%(value)s is not one of the categories."),
                code="invalid_choice",
                params={"value": str(item)[:20]},
            )
    if len(set(value)) != len(value):
        raise ValidationError(_("A category is listed once."), code="duplicate")
