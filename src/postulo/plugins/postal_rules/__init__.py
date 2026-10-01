"""Address rules, per country: what a part is called, what order the parts are written in,
what is expected -- and the little that is certain enough to refuse.

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

**Two things are refused, and everything else is still a note (#306).** Until #306 nothing
here refused: every rule warned, for the reason `phones.py` keeps an unparseable number as
typed -- BFPO addresses, rural routes, informal settlements, temporary accommodation, and a
table that is simply wrong about somewhere. An application that will not accept your address
is telling you something about who it was written for. That argument still decides what may
be refused, and it leaves two things: a postcode in a form its country never uses, and a
part that every address in that country carries left empty (`refusals_for`). Both are what
the country's own format rules out, not what this table merely finds unusual, and each is
said beside its part, with what the country expects. What is only *usually* so stays a note
(`notes_for`), exactly as before. And **a format check is not a verification**: nothing here
knows whether an address exists, or whether a town goes with a postcode.

**The second is asked of an address, and a town and a country are not one.** A row with no
street and no postcode is a place: what a CV shows, and all that somebody who keeps no
street here on purpose types. Nothing is required of it, and it is kept; `place_for` says
what a whole address in that country also carries, as a note. A postcode's form is checked
whenever one is typed. **And a street is required nowhere**: a PO box, a rural route, poste
restante and a major customer with a postcode of its own are all real addresses without
one, and where a guide gives a street's line an alternative, the alternative goes in the
same box.

**A refusal is a whole sentence, written once for each thing it can name** (`NEEDS`,
`WRITTEN`). A part's name is not dropped into it: "a address" and "A Eircode" are what that
gives in English, and no catalogue can repair the article and the case in a language where
they depend on the noun. Nor is the country named, because Postulo knows its name in English
only and the reader may be reading in another language: the sentence sits in the row,
beside the part, with the country the row shows. The two notes in `notes_for` are older and
do both of those things; that is #642, and it is left to it -- but for the empty street,
which had a sentence of its own while three countries refused it and keeps it as a note
(`STREET`).

**Switched off, everything falls back to what a country with no row already gets**: nothing
refused, no notes, neutral labels, and the parts in the order they were entered. That is the
same code path rather than a second one, so *off* is a state this is tested in rather than a
state nobody has seen.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from .rules import PARTS, Rule, lines_for, rule_for, written

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
            "Prefecture — draw the parts in that country's order, and check an address "
            "against its country before it is kept: a postcode in a form the country never "
            "uses is refused, and so is an address that leaves empty a part every address "
            "there has, with the reason beside it. A town and a country alone, with no "
            "street and no postcode, are kept as a place. Spaces, hyphens and capitals in "
            "a postcode are put right. Nothing checks that an address exists, addresses "
            "already kept stay as they are until they are changed, and a country with no "
            "rules is free-form. Switched off, every address is free-form with neutral "
            "labels."
        ),
    )
)
class PostalRulesFeature:
    """A declaration. `postulo.core.postal` asks the policy; this says what there is to ask
    about."""


#: Said under the address rows while the rules act for the reader: what is refused, and how
#: forgiving a postcode's spelling is. The plugin's own words, in its own catalogue, because
#: with the plugin switched off none of it is true and the page says none of it.
HELP = _(
    "Where Postulo knows a country's addresses, a postcode has to be in the form that "
    "country uses, and an address with a street or a postcode cannot leave empty the parts "
    "every address there has. A town and a country alone are kept as a place. Spaces, "
    "hyphens and capital letters in a postcode are put right as you save: 1000100 is kept "
    "as 1000-100. Nothing checks that an address exists, and a country Postulo has no rules "
    "for is free-form."
)

#: A required part left empty, one whole sentence for each thing a part can be called in a
#: country that requires it (#642 is why: a label is never pasted into a sentence). The key
#: is the label key the row gives the part, or the part's own name where it gives none.
#: Never the street: no country requires one.
NEEDS = {
    "municipality": _("An address in this country needs a town or city."),
    "post_town": _("An address in this country needs a post town."),
    "postcode": _("An address in this country needs a postcode, written like %(example)s."),
    "zip": _("An address in this country needs a ZIP code, written like %(example)s."),
    "plz": _("An address in this country needs a postcode (PLZ), written like %(example)s."),
    "cap": _("An address in this country needs a postcode (CAP), written like %(example)s."),
    "cep": _("An address in this country needs a postcode (CEP), written like %(example)s."),
    "state": _("An address in this country needs a state."),
    "province": _("An address in this country needs a province."),
}

#: For a part a later row requires and nobody wrote a sentence for. A test holds the table
#: to `NEEDS`, so this is what a gap says rather than what any row says today.
NEEDS_THIS = _("An address in this country needs this part.")

#: An empty street, which is a note everywhere and a refusal nowhere. One whole sentence,
#: as a refusal is: it was one, in the three countries that required a street, and it names
#: what a street's box also holds, so that nobody with a PO box thinks they have nothing to
#: put there.
STREET = _(
    "An address in this country usually gives a street and number, or else the place "
    "where post is delivered there."
)

#: A postcode in a form its country never uses, one whole sentence for each thing a
#: postcode is called. An example is far more use than a description of the pattern.
WRITTEN = {
    "postcode": _("A postcode in this country is written like %(example)s."),
    "zip": _("A ZIP code is written like %(example)s."),
    "eircode": _("An Eircode is written like %(example)s."),
    "plz": _("A postcode (PLZ) in this country is written like %(example)s."),
    "cap": _("A postcode (CAP) in this country is written like %(example)s."),
    "cep": _("A postcode (CEP) in this country is written like %(example)s."),
}


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


def _parts_of(address) -> tuple[str, dict[str, str]]:
    """The country's code and each part as it was typed, less the space around it."""
    country = (getattr(address, "country", "") or "").strip().upper()
    return country, {part: (getattr(address, part, "") or "").strip() for part in PARTS}


def canonical(postcode: str, country: str) -> str:
    """The postcode as its country writes it, or as it was typed where it is not one of
    theirs -- or the country has no rules, and so no way of writing one that is known here.

    ``1000100`` and ``1000 100`` are Portugal's ``1000-100``; ``1234ab`` is the
    Netherlands' ``1234 AB``. Never a guess past case, spaces, hyphens and full stops.
    """
    typed = (postcode or "").strip()
    return written(typed, country) or typed


def _is_a_place(parts: dict[str, str]) -> bool:
    """No street and no postcode: a town and a country, and nothing to post to."""
    return not parts["street"] and not parts["postcode"]


def _missing(rule: Rule, parts: dict[str, str]) -> list[tuple[str, object]]:
    """Each part every address in that country carries and this one leaves empty, with the
    sentence that says so."""
    example = {"example": rule.postcode_example}
    return [
        (part, NEEDS.get(rule.calls.get(part, part), NEEDS_THIS) % example)
        for part in rule.requires
        if not parts[part]
    ]


def refusals_for(address) -> list[tuple[str, object]]:
    """What the country's own format rules out about this address: the part, and a sentence
    saying what the country expects of it.

    Two things, and no more (#306): a part every address there carries left empty, and a
    postcode in a form the country never uses. Empty for a country with no row, for an
    address with no country, and for one that fits. Whether the address exists, and
    whether the town goes with the postcode, is nothing this can know.

    The first is asked only of a row with a street or a postcode. One with neither is a
    place and not an address -- *Lisboa, Portugal* is what a CV shows, and what somebody
    types who keeps no street here on purpose -- and nothing is required of a place.
    """
    country, parts = _parts_of(address)
    if not country:
        return []
    rule = rule_for(country)

    found = [] if _is_a_place(parts) else _missing(rule, parts)
    postcode = parts["postcode"]
    if postcode and rule.postcode and not rule.postcode_open and written(postcode, country) is None:
        sentence = WRITTEN.get(rule.calls.get("postcode", "postcode"), WRITTEN["postcode"])
        found.append(("postcode", sentence % {"example": rule.postcode_example}))
    return found


def place_for(address) -> list[tuple[str, object]]:
    """What an address in that country also carries, where this row is a place: the part,
    and a sentence.

    A town or a region with a country, and no street and no postcode. It is kept as it is
    and nothing about it is refused; this is what a page says under it, so that somebody
    who meant to write a whole address knows what one has there: the street, as a note, and
    the parts a fuller row would be refused for. Empty for a whole address, for a country
    with no row, and for a row that holds nothing but its country, which says nothing about
    what its owner meant.
    """
    country, parts = _parts_of(address)
    rule = rule_for(country)
    if not rule.expects or not _is_a_place(parts):
        return []
    if not parts["municipality"] and not parts["region"]:
        return []
    street = [("street", STREET)] if "street" in rule.expects else []
    return street + _missing(rule, parts)


def notes_for(address) -> list[tuple[str, object]]:
    """What looks unusual about this address for its country: the part, and a sentence.

    Never an error. Empty for a country with no row, for an address with no country, and for
    one that fits. Each sentence says what is normally expected rather than what is wrong,
    because the table is the thing more likely to be wrong than the person. A postcode that
    is the country's own in everything but its spaces, hyphens and capitals is not unusual.
    """
    country, parts = _parts_of(address)
    if not country:
        return []
    rule = rule_for(country)

    found: list[tuple[str, object]] = []
    for part in rule.expects:
        if parts[part]:
            continue
        if part == "street":
            found.append((part, STREET))
            continue
        found.append(
            (
                part,
                _("Post to %(country)s usually needs a %(part)s.")
                % {
                    "country": _country_name(country),
                    "part": _lower(label_for(part, country)),
                },
            )
        )

    postcode = parts["postcode"]
    if postcode and rule.postcode and written(postcode, country) is None:
        found.append(
            (
                "postcode",
                _("A %(name)s in %(country)s usually looks like %(example)s.")
                % {
                    "name": _lower(label_for("postcode", country)),
                    "country": _country_name(country),
                    "example": rule.postcode_example,
                },
            )
        )
    return found


def warnings_for(address) -> list:
    """What looks unusual about this address for its country, as sentences. Never an error."""
    return [sentence for _part, sentence in notes_for(address)]


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
    "HELP",
    "LABELS",
    "NEEDS",
    "POSTAL_RULES",
    "STREET",
    "WRITTEN",
    "PostalRulesFeature",
    "Rule",
    "canonical",
    "expects",
    "label_for",
    "lines_for",
    "notes_for",
    "place_for",
    "refusals_for",
    "render",
    "rule_for",
    "warnings_for",
]
