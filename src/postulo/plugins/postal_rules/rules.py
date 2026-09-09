"""What an address looks like in each country: what it is called, and what order it prints in.

A **curated table**, which is the shape this codebase already uses for everything of this
kind — `phones.COUNTRIES`, `NATIVE_NAMES`, `PLURAL_FORMS`, `FLAG_COUNTRIES` — each entry
decided rather than derived, and several carrying a note about the trap in that particular
place. It needs no third-party licence, no vendored snapshot and no update pipeline, and it
grows the way the languages did: deliberately, in order, with the reasoning per entry (#147).

**What was ruled out, so nobody proposes it again.** Google's `libaddressinput` is the set
everybody reaches for, and Postulo does not depend on Google code — a project that exists as
an alternative to services answering to somebody else's jurisdiction does not put that
jurisdiction in its address form. Its data is also served from a Google endpoint, which
Postulo could not call at render time in any case.

**What is borrowed, and it is an idea rather than a file.** OpenCage's `address-formatting`
is MIT and covers 251 territories with templates and test cases, and it does exactly one
thing: renders the parts in the right order for a country. The ``order`` column below is that
idea, hand-written for the places Postulo speaks the language of. Their work is worth reading
before extending this.

**It fails in the right direction.** A country with no row gets no rules: free-form entry, no
warnings, and the parts printed in the order they were entered. That is the same answer this
issue requires anyway, so an absent row is a gap and never a bug.

**And nothing here refuses.** The rules *warn*. `phones.py` keeps an unparseable number
exactly as typed and the same holds here — BFPO addresses, rural routes, informal
settlements, temporary accommodation, and simply a table that is wrong about somewhere. An
application that will not accept your address is telling you something about who it was
written for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: When this table was last reviewed against national postal guidance. A data set records
#: when it was taken, exactly as #140 concludes for NACE: a reader deserves to know whether
#: they are looking at something current or something from four years ago.
REVIEWED = "2026-09"

#: The parts, by the name they have on the model.
PARTS = ("street", "postcode", "municipality", "region", "country")


@dataclass(frozen=True)
class Rule:
    """What one country expects. Every field is advice; none of it refuses a save."""

    #: The parts that country's post normally needs. A warning names what is missing.
    expects: tuple[str, ...] = ()
    #: A pattern a postcode there normally matches, or empty where a check is not worth
    #: making. Never anchored to a lookup: this says *looks unlike*, never *does not exist*.
    postcode: str = ""
    #: An example, shown in the warning. Far more use than a regular expression.
    postcode_example: str = ""
    #: What to call each part in this country. Keys are part names; values are label keys
    #: that `labels.py` turns into words in the *reader's* language.
    calls: dict[str, str] = field(default_factory=dict)
    #: The order the parts print in. Empty means the order they were entered in.
    order: tuple[str, ...] = ()
    #: Why this row says what it says, where that is not obvious.
    note: str = ""


#: The default, for a country with no row of its own: expects nothing, checks nothing, and
#: prints in the order the parts were entered. A country Postulo has no rules for gets no
#: rules — which is the same thing this issue asks for anyway, so an absent row is a gap and
#: never a refusal.
ANYWHERE = Rule(expects=())

#: Countries whose language Postulo speaks, plus the ones its people most often apply to.
#: Alphabetical by code. A missing country is a gap to fill, never a country refused.
RULES: dict[str, Rule] = {
    "AT": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="1010",
        calls={"postcode": "plz", "region": "state"},
        order=("street", "postcode municipality", "country"),
        note="Bundesland is written only where the town name is ambiguous.",
    ),
    "BE": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="1000",
        order=("street", "postcode municipality", "country"),
    ),
    "BG": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="1000",
        order=("street", "postcode municipality", "country"),
    ),
    "CH": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="8001",
        calls={"postcode": "plz", "region": "canton"},
        order=("street", "postcode municipality", "country"),
    ),
    "CZ": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{3} ?\d{2}$",
        postcode_example="110 00",
        order=("street", "postcode municipality", "country"),
    ),
    "DE": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="10115",
        calls={"postcode": "plz", "region": "state"},
        order=("street", "postcode municipality", "country"),
        note="Bundesland is not written on ordinary post.",
    ),
    "DK": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="1050",
        order=("street", "postcode municipality", "country"),
    ),
    "EE": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="10111",
        order=("street", "postcode municipality", "country"),
    ),
    "ES": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="28001",
        calls={"region": "province"},
        order=("street", "postcode municipality", "region", "country"),
        note="The province is written in brackets after the town where they differ.",
    ),
    "FI": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="00100",
        order=("street", "postcode municipality", "country"),
    ),
    "FR": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="75001",
        order=("street", "postcode municipality", "country"),
    ),
    "GB": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$",
        postcode_example="SW1A 1AA",
        calls={"municipality": "post_town", "region": "county"},
        order=("street", "municipality", "postcode", "country"),
        note="The post town is written in capitals, and the county is optional since 1996.",
    ),
    "GR": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{3} ?\d{2}$",
        postcode_example="104 31",
        order=("street", "postcode municipality", "country"),
    ),
    "HR": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="10000",
        order=("street", "postcode municipality", "country"),
    ),
    "HU": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="1051",
        order=("municipality", "street", "postcode", "country"),
        note="Hungary writes the settlement first and the postcode last.",
    ),
    "IE": Rule(
        expects=("street", "municipality"),
        postcode=r"^[A-Z\d]{3} ?[A-Z\d]{4}$",
        postcode_example="D02 AF30",
        calls={"postcode": "eircode", "region": "county"},
        order=("street", "municipality", "region", "postcode", "country"),
        note=(
            "Eircode arrived in 2015 and plenty of addresses predate it, so it is expected "
            "of nobody. The county is what post has always been sorted by."
        ),
    ),
    "IS": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{3}$",
        postcode_example="101",
        order=("street", "postcode municipality", "country"),
    ),
    "IT": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="00184",
        calls={"postcode": "cap", "region": "province"},
        order=("street", "postcode municipality region", "country"),
        note="The two-letter province follows the town in brackets.",
    ),
    "LT": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^(LT-)?\d{5}$",
        postcode_example="LT-01100",
        order=("street", "postcode municipality", "country"),
    ),
    "LU": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^(L-)?\d{4}$",
        postcode_example="L-1111",
        order=("street", "postcode municipality", "country"),
    ),
    "LV": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^(LV-)?\d{4}$",
        postcode_example="LV-1050",
        order=("street", "municipality", "postcode", "country"),
    ),
    "MT": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^[A-Z]{3} ?\d{4}$",
        postcode_example="VLT 1117",
        order=("street", "municipality", "postcode", "country"),
    ),
    "NL": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4} ?[A-Z]{2}$",
        postcode_example="1012 JS",
        order=("street", "postcode municipality", "country"),
    ),
    "NO": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}$",
        postcode_example="0150",
        order=("street", "postcode municipality", "country"),
    ),
    "PL": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{2}-\d{3}$",
        postcode_example="00-001",
        order=("street", "postcode municipality", "country"),
    ),
    "PT": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{4}-\d{3}$",
        postcode_example="1000-001",
        calls={"region": "district"},
        order=("street", "postcode municipality", "country"),
        note="The four-three form has been standard since 1994.",
    ),
    "RO": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{6}$",
        postcode_example="010101",
        calls={"region": "county"},
        order=("street", "postcode municipality", "region", "country"),
    ),
    "SE": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{3} ?\d{2}$",
        postcode_example="111 29",
        order=("street", "postcode municipality", "country"),
    ),
    "SI": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^(SI-)?\d{4}$",
        postcode_example="1000",
        order=("street", "postcode municipality", "country"),
    ),
    "SK": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{3} ?\d{2}$",
        postcode_example="811 01",
        order=("street", "postcode municipality", "country"),
    ),
    "TR": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="34000",
        calls={"region": "province"},
        order=("street", "postcode municipality region", "country"),
    ),
    "UA": Rule(
        expects=("street", "postcode", "municipality"),
        postcode=r"^\d{5}$",
        postcode_example="01001",
        calls={"region": "oblast"},
        order=("street", "municipality", "region", "postcode", "country"),
    ),
    # Not a country Postulo speaks the language of, and the one people most often apply to
    # from one that is. Left out, it would be the country whose form is most often wrong.
    "US": Rule(
        expects=("street", "postcode", "municipality", "region"),
        postcode=r"^\d{5}(-\d{4})?$",
        postcode_example="20500",
        calls={"postcode": "zip", "region": "state"},
        order=("street", "municipality region postcode", "country"),
        note="The state is part of the address rather than an optional refinement.",
    ),
    "CA": Rule(
        expects=("street", "postcode", "municipality", "region"),
        postcode=r"^[A-Z]\d[A-Z] ?\d[A-Z]\d$",
        postcode_example="K1A 0A6",
        calls={"region": "province"},
        order=("street", "municipality region postcode", "country"),
    ),
    "AU": Rule(
        expects=("street", "postcode", "municipality", "region"),
        postcode=r"^\d{4}$",
        postcode_example="2600",
        calls={"region": "state"},
        order=("street", "municipality region postcode", "country"),
    ),
    "BR": Rule(
        expects=("street", "postcode", "municipality", "region"),
        postcode=r"^\d{5}-?\d{3}$",
        postcode_example="01310-100",
        calls={"postcode": "cep", "region": "state"},
        order=("street", "municipality region", "postcode", "country"),
    ),
    "JP": Rule(
        expects=("street", "postcode", "municipality", "region"),
        postcode=r"^\d{3}-?\d{4}$",
        postcode_example="100-8111",
        calls={"region": "prefecture"},
        order=("postcode", "region municipality", "street", "country"),
        note="Japan writes largest first, which is the reverse of most of this table.",
    ),
}


def rule_for(country: str) -> Rule:
    """The rules for a country, or the ones that ask nothing and refuse nothing."""
    return RULES.get((country or "").strip().upper(), ANYWHERE)
