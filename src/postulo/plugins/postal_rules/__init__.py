"""Address rules, per country: what a field is called, what is expected, what order it prints.

> another internet plugin, multiple postal address, not unique across instance, validades is
> made on a contry basis inside the plugin

The rules half of #92, and the whole of #147. The model is Postulo's — the parts of an
address are the same five everywhere — and what differs by country lives here, in a table
`rules.py` maintains the way this codebase maintains every table of its kind.

**Naming the fields turned out to be harder than checking them, and it is not a translation
problem.** A person reading Postulo in Portuguese who enters a United States address should
see *Estado*; entering a Portuguese one, *Distrito*; a Japanese one, *Prefeitura*. The label
depends on **the address's country** and the language depends on **the reader**, and both
vary at once — nothing else in this codebase has that shape, because every other string is
chosen by the reader's language alone. So the table names a *key* and this module turns the
key into a word, which the reader's catalogue then translates.

**Nothing here refuses.** Every rule warns. `phones.py` keeps an unparseable number exactly
as typed and the same holds for an address: BFPO addresses, rural routes, informal
settlements, temporary accommodation, and a table that is simply wrong about somewhere. An
application that will not accept your address is telling you something about who it was
written for, and that is a matter of dignity before it is one of correctness.

**Switched off, everything falls back to what a country with no row already gets**: no
warnings, neutral labels, and the parts printed in the order they were entered. That is the
same code path rather than a second one, so *off* is a state this is tested in rather than a
state nobody has seen.
"""

from __future__ import annotations

import re

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from .rules import PARTS, Rule, rule_for

#: The identifier the policy rows key on.
POSTAL_RULES = "postal-rules"

#: What each label key says, in the *reader's* language. The key is chosen by the address's
#: country; the words are chosen by whoever is reading. That split is the whole point.
#:
#: Some of these stay as they are in every language, because they are names rather than
#: descriptions: an Eircode is an Eircode in Lisbon, and a CAP is a CAP in Dublin.
LABELS = {
    # what a region is called
    "region": _("Region"),
    "state": _("State"),
    "province": _("Province"),
    "county": _("County"),
    "district": _("District"),
    "prefecture": _("Prefecture"),
    "canton": _("Canton"),
    "oblast": _("Oblast"),
    # what a postcode is called
    "postcode": _("Postcode"),
    "zip": _("ZIP code"),
    "eircode": _("Eircode"),
    "plz": _("Postcode (PLZ)"),
    "cap": _("Postcode (CAP)"),
    "cep": _("Postcode (CEP)"),
    # what a town is called
    "municipality": _("Town or city"),
    "post_town": _("Post town"),
}

#: The label a part has when its country says nothing about it.
NEUTRAL = {
    "street": _("Address"),
    "postcode": "postcode",
    "municipality": "municipality",
    "region": "region",
    "country": _("Country"),
}


@declares(
    shipped(
        name=POSTAL_RULES,
        label=_("Address rules by country"),
        kind="feature",
        description=_(
            "Name each part of an address the way its own country does — State, Distrito, "
            "Prefecture — and say when something usually expected is missing. Nothing is "
            "ever refused: an address that does not fit the rules is saved exactly as "
            "typed, because plenty of real addresses do not fit any rule. Switched off, "
            "every address is free-form with neutral labels."
        ),
    )
)
class PostalRulesFeature:
    """A declaration. `postulo.core.postal` asks the policy; this says what there is to ask
    about."""


def label_for(part: str, country: str):
    """What to call one part of an address from ``country``, in the reader's language."""
    if part == "street" or part == "country":
        return NEUTRAL[part]
    key = rule_for(country).calls.get(part) or NEUTRAL.get(part, part)
    return LABELS.get(key, LABELS.get(part, key))


def expects(part: str, country: str) -> bool:
    """Whether post in that country normally needs this part.

    Read by the form to mark a field, never to refuse one. A field somebody leaves empty is
    a warning at most.
    """
    return part in rule_for(country).expects


def warnings_for(address) -> list:
    """What looks unusual about this address for its country. Never an error.

    Empty for a country with no row, for an address with no country, and for one that fits.
    Each sentence says what is normally expected rather than what is wrong, because the
    table is the thing more likely to be wrong than the person.
    """
    country = (getattr(address, "country", "") or "").strip().upper()
    if not country:
        return []
    rule = rule_for(country)
    if rule is None:
        return []

    found: list = []
    for part in rule.expects:
        if not (getattr(address, part, "") or "").strip():
            found.append(
                _("Post to %(country)s usually needs a %(part)s.")
                % {"country": _country_name(country), "part": _lower(label_for(part, country))}
            )

    postcode = (getattr(address, "postcode", "") or "").strip()
    if postcode and rule.postcode and not re.match(rule.postcode, postcode.upper()):
        found.append(
            _("A %(name)s in %(country)s usually looks like %(example)s.")
            % {
                "name": _lower(label_for("postcode", country)),
                "country": _country_name(country),
                "example": rule.postcode_example,
            }
        )
    return found


def render(address) -> list[str]:
    """The parts in the order that country writes them, as lines.

    A country with no row gets the order they were entered in, which is what an address with
    no rules has always had and is never wrong enough to matter.
    """
    code = (getattr(address, "country", "") or "").strip().upper()
    values = {part: (getattr(address, part, "") or "").strip() for part in PARTS}
    # The country prints as its name, not its code: a letter is addressed to Portugal, not
    # to PT. Everything else prints as it was typed.
    values["country"] = _country_name(code)

    order = rule_for(code).order or PARTS
    lines = []
    for group in order:
        joined = " ".join(values.get(part, "") for part in group.split()).strip()
        joined = " ".join(joined.split())
        if joined:
            lines.append(joined)
    return lines


def _country_name(code: str) -> str:
    from postulo.core import phones

    return phones.country_name(code) if len(code) == 2 else code


def _lower(label) -> str:
    """A label mid-sentence. Kept as it is where the word is a name rather than a noun."""
    text = str(label)
    return text if text.isupper() or " (" in text or text in {"Eircode"} else text.lower()


__all__ = [
    "LABELS",
    "POSTAL_RULES",
    "PostalRulesFeature",
    "Rule",
    "expects",
    "label_for",
    "render",
    "rule_for",
    "warnings_for",
]
