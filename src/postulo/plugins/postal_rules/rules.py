"""What an address looks like in each country: what its parts are called, the order a form
draws them in, the form its postcode takes and the parts it cannot do without.

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
thing: puts the parts in the right order for a country. The ``order`` column below is that
idea, which the form draws its lines from (``lines_for``), hand-written for the places
Postulo speaks the language of. Their work is worth reading before extending this.

**It fails in the right direction.** A country with no row gets no rules: free-form entry, no
warnings, nothing refused, and the parts in the order they were entered. An absent row is a
gap and never a bug.

**Two columns refuse, and the rest still warn (#306).** Until #306 nothing here refused
anything. What a row may refuse now is narrow, and it is what the country's own format rules
out: a postcode in a form that country never uses (``postcode``), and a part every address
there carries left empty (``requires``). Everything else a row knows — what is *usually*
there (``expects``), what a postcode usually looks like where the operator itself says codes
exist outside the pattern (``postcode_open``) — is still said as a note and never stops a
save. BFPO addresses, rural routes, informal settlements, temporary accommodation: **a
street is required nowhere.** Three guides read as though an address always had one
(Belgium's, Italy's, the United States'), and every alternative they show -- a PO box, a
rural route, poste restante, a major customer with a postcode of its own -- goes in the same
box, so the requirement only ever refused an address left without a street on purpose.

**``requires`` is asked of an address, and a town and a country are not one.** A row with no
street and no postcode is a place: what a CV shows, and what somebody who keeps no street
here on purpose types. Nothing is required of it. The form of a postcode is checked whenever
one is typed.

**A pattern is the country's own alphabet, never "any seven characters".** A word typed in
the postcode's box -- *Dublin 4* is a real habit in Ireland's -- must be refused or left
exactly as it was typed, and never taken for a code and regrouped into one. A test holds
every row to that.

**A postcode is compared folded.** Spaces, hyphens and full stops are taken out and the
letters a to z are made capitals before the pattern is asked, and the country's own code in
front (``PT-1000-100``, ``DE-10115``) goes with them, because the country is a part of its
own here: its ISO code, and the older code post from abroad was once addressed with
(``D-10115``, ``F-75001``; ``postcode_once``), each only under its own country. So
``1000100``, ``1000 100`` and ``1000-100`` are all Portugal's ``1000-100``, and ``1234ab``
is the Netherlands' ``1234 AB``: what matches is written back the way the country writes it
(``postcode_joins``, ``postcode_prefix``).

**Where each row comes from** is beside it (``source``): the Universal Postal Union's
*Postal addressing systems* guide for that country, one sheet per member, each dated by the
UPU and written with the national operator named on it. The patterns and the required parts
were written here from those sheets, read in October 2026. **No data set was copied**: not
Google's, for the reason above, and no third party's compiled list of expressions either,
whose licences vary by file. Nothing is fetched; the table ships with the plugin.
"""

from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass, field

#: When this table was last reviewed against national postal guidance. A data set records
#: when it was taken, exactly as #140 concludes for NACE: a reader deserves to know whether
#: they are looking at something current or something from four years ago.
REVIEWED = "2026-10"

#: The parts, by the name they have on the model.
PARTS = ("street", "postcode", "municipality", "region", "country")

#: Where the Universal Postal Union publishes its guide to each member's addresses.
UPU_GUIDES = "https://www.upu.int/UPU/media/upu/PostalEntitiesFiles/addressingUnit/"


def upu(sheet: str, dated: str) -> str:
    """A row's source: the UPU's guide for that country, and the date the UPU put on it."""
    return f"{UPU_GUIDES}{sheet}En.pdf ({dated})"


@dataclass(frozen=True)
class Rule:
    """What one country expects, and the little of it that is certain enough to refuse."""

    #: The parts that country's post normally needs. A note names what is missing.
    expects: tuple[str, ...] = ()
    #: The parts every address there carries, by the country's own guide. Left empty on a
    #: row that has a street or a postcode, one of these is refused. Always among
    #: ``expects``, and usually fewer; never the street.
    requires: tuple[str, ...] = ()
    #: The pattern a postcode there matches, **over its folded form**: capitals, with no
    #: space, hyphen or full stop (`fold`). Its groups are what `written` joins back
    #: together. Empty where the country has no rules. Never anchored to a lookup: this
    #: says *no postcode there is written so*, never *this one does not exist*.
    postcode: str = ""
    #: What goes between the groups when the postcode is written the country's way.
    postcode_joins: str = ""
    #: What the country writes before it, where its own form has a prefix.
    postcode_prefix: str = ""
    #: What the country's postcodes were once written with in front, besides its ISO code:
    #: the code of international post from before ISO's was used (``D-10115``). Taken off
    #: as the ISO code is, and only under this country.
    postcode_once: tuple[str, ...] = ()
    #: The operator itself says codes exist outside the pattern, so a postcode unlike it is
    #: a note and never a refusal.
    postcode_open: bool = False
    #: An example, shown in the sentence. Far more use than a regular expression.
    postcode_example: str = ""
    #: What to call each part in this country. Keys are part names; values are label keys
    #: that `LABELS` turns into words in the *reader's* language.
    calls: dict[str, str] = field(default_factory=dict)
    #: The order the parts are written in, which the form draws its lines in (`lines_for`).
    #: Empty means the lines it always drew.
    order: tuple[str, ...] = ()
    #: Why this row says what it says, where that is not obvious.
    note: str = ""
    #: Where the postcode's form and the required parts were read.
    source: str = ""


#: The default, for a country with no row of its own: expects nothing, checks nothing,
#: refuses nothing, and keeps the order the parts were entered in. A country Postulo has no
#: rules for gets no rules, so an absent row is a gap and never a refusal.
ANYWHERE = Rule(expects=())

#: Countries whose language Postulo speaks, plus the ones its people most often apply to.
#: Alphabetical by code. A missing country is a gap to fill, never a country refused.
RULES: dict[str, Rule] = {
    "AT": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_once=("A",),
        postcode_example="1010",
        calls={"postcode": "plz", "region": "state"},
        order=("street", "postcode municipality", "country"),
        note="Bundesland is written only where the town name is ambiguous.",
        source=upu("aut", "04/2014"),
    ),
    "BE": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_once=("B",),
        postcode_example="1000",
        order=("street", "postcode municipality", "country"),
        note=(
            "The guide gives an address a minimum of three lines: the addressee, the "
            "delivery point and the postcode with the town. The delivery point may be a PO "
            "box, and it goes in the same box as a street, so nothing is required of it. "
            "The guide also wants no B- or BE- in front of the postcode, so both are taken "
            "off."
        ),
        source=upu("bel", "09/2012"),
    ),
    "BG": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_example="1000",
        order=("street", "postcode municipality", "country"),
        source=upu("bgr", "05/2017"),
    ),
    "CH": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_example="8001",
        calls={"postcode": "plz", "region": "canton"},
        order=("street", "postcode municipality", "country"),
        source=upu("che", "06/2024"),
    ),
    "CZ": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{3})(\d{2})",
        postcode_joins=" ",
        postcode_example="110 00",
        order=("street", "postcode municipality", "country"),
        source=upu("cze", "02/2018"),
    ),
    "DE": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_once=("D",),
        postcode_example="10115",
        calls={"postcode": "plz", "region": "state"},
        order=("street", "postcode municipality", "country"),
        note=(
            "Bundesland is not written on ordinary post. The guide: on no account a country "
            "code (D- or DE-) in front of the postcode. So both are taken off."
        ),
        source=upu("deu", "01/2015"),
    ),
    "DK": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_example="1050",
        order=("street", "postcode municipality", "country"),
        source=upu("dnk", "05/2024"),
    ),
    "EE": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_example="10111",
        order=("street", "postcode municipality", "country"),
        source=upu("est", "06/2014"),
    ),
    "ES": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_once=("E",),
        postcode_example="28001",
        calls={"region": "province"},
        order=("street", "postcode municipality", "region", "country"),
        note="The province is written in brackets after the town where they differ.",
        source=upu("esp", "01/2013"),
    ),
    "FI": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(AX)?(\d{5})",
        postcode_joins="-",
        postcode_once=("FIN",),
        postcode_example="00100",
        order=("street", "postcode municipality", "country"),
        note=(
            "The Åland Islands follow Finland's system, and the UPU's sheet for them "
            "(alaEn.pdf, 02/2019) asks for AX before the postcode: AX-22100 MARIEHAMN. "
            "Somebody there may have chosen Finland, so the code is taken with its AX and "
            "written back with it."
        ),
        source=upu("fin", "11/2024"),
    ),
    "FR": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_once=("F",),
        postcode_example="75001",
        order=("street", "postcode municipality", "country"),
        source=upu("fra", "09/2011"),
    ),
    "GB": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode",),
        postcode=(
            r"([A-Z]{1,2}\d[A-Z\d]?|GIR)(\d[A-Z]{2})"
            r"|(BFPO)(\d{1,4})"
            r"|(ASCN|STHL|TDCU|PCRN|SIQQ|BIQQ|BBND|FIQQ|TKCA)(1ZZ)"
        ),
        postcode_joins=" ",
        postcode_example="SW1A 1AA",
        calls={"municipality": "post_town", "region": "county"},
        order=("street", "municipality", "postcode", "country"),
        note=(
            "The post town is written in capitals, and the county is optional since 1996. "
            "The guide lists six forms -- AN NAA, ANN NAA, AAN NAA, AANN NAA, ANA NAA, AANA "
            "NAA -- and one exception, GIR 0AA. Two things it does not show are written "
            "under this country too. A forces address ends in BFPO and a number of one to "
            "four digits where the post town and the postcode would be, so BFPO 61 is a "
            "postcode here and the post town is required of nobody: #147 named BFPO as the "
            "address that must not be refused. And the overseas territories the guide lists "
            "have one code each, written like a postcode -- ASCN 1ZZ for Ascension, STHL 1ZZ "
            "for Saint Helena, TDCU 1ZZ for Tristan da Cunha, PCRN 1ZZ for Pitcairn, SIQQ "
            "1ZZ for South Georgia, BIQQ 1ZZ and BBND 1ZZ for the Antarctic and the Indian "
            "Ocean territories, FIQQ 1ZZ for the Falklands, TKCA 1ZZ for the Turks and "
            "Caicos -- and several of them have no entry of their own in the country list, "
            "so their people have only this one."
        ),
        source=upu("gbr", "11/2011"),
    ),
    "GR": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{3})(\d{2})",
        postcode_joins=" ",
        postcode_example="104 31",
        order=("street", "postcode municipality", "country"),
        source=upu("grc", "03/2005"),
    ),
    "HR": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_example="10000",
        order=("street", "postcode municipality", "country"),
        note=(
            "The guide asks for HR- before the postcode on post from other countries only, "
            "so the form here is the five digits."
        ),
        source=upu("hrv", "05/2014"),
    ),
    "HU": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_once=("H",),
        postcode_example="1051",
        order=("municipality", "street", "postcode", "country"),
        note="Hungary writes the settlement first and the postcode last.",
        source=upu("hun", "01/2012"),
    ),
    "IE": Rule(
        expects=("street", "municipality"),
        requires=("municipality",),
        postcode=r"([AC-FHKNPRTV-Y]\d{2}|D6W)([\dAC-FHKNPRTV-Y]{4})",
        postcode_joins=" ",
        postcode_example="D02 AF30",
        calls={"postcode": "eircode", "region": "county"},
        order=("street", "municipality", "region", "postcode", "country"),
        note=(
            "Eircode arrived in 2015 and plenty of addresses predate it, so it is required "
            "of nobody. The county is what post has always been sorted by. The guide: seven "
            "alphanumeric characters with a space after the third. Any seven is not an "
            "Eircode, though: the routing key is a letter and two digits, or D6W, and the "
            "letters are fifteen -- A, C, D, E, F, H, K, N, P, R, T, V, W, X, Y -- in the "
            "key and in the four characters after it (Eircode's own product guide). Asked "
            "of any seven characters, 'Dublin 4', which people do write in this box, was "
            "taken for a code and kept as 'DUB LIN4'."
        ),
        source=upu("irl", "06/2017"),
    ),
    "IS": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{3})",
        postcode_example="101",
        order=("street", "postcode municipality", "country"),
        source=upu("isl", "07/2002"),
    ),
    "IT": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_once=("I",),
        postcode_example="00184",
        calls={"postcode": "cap", "region": "province"},
        order=("street", "postcode municipality region", "country"),
        note=(
            "The two-letter province follows the town in brackets. The guide marks the line "
            "with the street and the line with the postcode and the town as mandatory; the "
            "first of them may hold a PO box number (casella postale) or a village "
            "(frazione) instead, which go in the same box as a street, so nothing is "
            "required of it."
        ),
        source=upu("ita", "04/2020"),
    ),
    "LT": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_prefix="LT-",
        postcode_example="LT-01100",
        order=("street", "postcode municipality", "country"),
        note="The guide writes the postcode with LT- before it, and so does this table.",
        source=upu("ltu", "06/2018"),
    ),
    "LU": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"L?(\d{4})",
        postcode_prefix="L-",
        postcode_example="L-1111",
        order=("street", "postcode municipality", "country"),
        note="The guide writes the postcode with L- before it, and so does this table.",
        source=upu("lux", "06/2013"),
    ),
    "LV": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_prefix="LV-",
        postcode_example="LV-1050",
        order=("street", "municipality", "postcode", "country"),
        note="The guide writes the postcode with LV- before it, after the town and a comma.",
        source=upu("lva", "09/2023"),
    ),
    "MT": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"([A-Z]{3})(\d{4})",
        postcode_joins=" ",
        postcode_open=True,
        postcode_example="VLT 1117",
        order=("street", "municipality", "postcode", "country"),
        note=(
            "The guide: personal postcodes are in use in Malta and may not follow this "
            "form. So the form is never a reason to refuse one here, only to say what one "
            "usually is."
        ),
        source=upu("mlt", "01/2013"),
    ),
    "NL": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})([A-Z]{2})",
        postcode_joins=" ",
        postcode_example="1012 JS",
        order=("street", "postcode municipality", "country"),
        source=upu("nld", "05/2016"),
    ),
    "NO": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_once=("N",),
        postcode_example="0150",
        order=("street", "postcode municipality", "country"),
        note=(
            "The guide shows an address with no street and no place: a name, then the "
            "postcode and the post office. A street is required of nobody here."
        ),
        source=upu("nor", "01/2013"),
    ),
    "PL": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{2})(\d{3})",
        postcode_joins="-",
        postcode_example="00-001",
        order=("street", "postcode municipality", "country"),
        source=upu("pol", "08/2018"),
    ),
    "PT": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})(\d{3})",
        postcode_joins="-",
        postcode_once=("P",),
        postcode_example="1000-001",
        calls={"region": "district"},
        order=("street", "postcode municipality", "country"),
        note="The four-three form has been standard since 1994.",
        source=upu("prt", "10/2024"),
    ),
    "RO": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{6})",
        postcode_example="010101",
        calls={"region": "county"},
        order=("street", "postcode municipality", "region", "country"),
        source=upu("rou", "10/2007"),
    ),
    "SE": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{3})(\d{2})",
        postcode_joins=" ",
        postcode_once=("S",),
        postcode_example="111 29",
        order=("street", "postcode municipality", "country"),
        source=upu("swe", "11/2024"),
    ),
    "SI": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{4})",
        postcode_example="1000",
        order=("street", "postcode municipality", "country"),
        note="The guide shows a major customer with its own postcode and no street.",
        source=upu("svn", "05/2016"),
    ),
    "SK": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{3})(\d{2})",
        postcode_joins=" ",
        postcode_example="811 01",
        order=("street", "postcode municipality", "country"),
        source=upu("svk", "01/2013"),
    ),
    "TR": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("municipality",),
        postcode=r"(\d{5})(\d{2})?",
        postcode_joins="-",
        postcode_example="34000",
        calls={"region": "province"},
        order=("street", "postcode municipality region", "country"),
        note=(
            "The guide shows a postcode with a sub-locality number after it, 06050-01, and "
            "that is accepted too. It also shows an address with no postcode at all -- "
            "poste restante, then the post office, the district and the province -- so the "
            "postcode is expected here and required of nobody."
        ),
        source=upu("tur", "07/2022"),
    ),
    "UA": Rule(
        expects=("street", "postcode", "municipality"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{5})",
        postcode_example="01001",
        calls={"region": "oblast"},
        order=("street", "municipality", "region", "postcode", "country"),
        source=upu("ukr", "07/2023"),
    ),
    # Not a country Postulo speaks the language of, and the one people most often apply to
    # from one that is. Left out, it would be the country whose form is most often wrong.
    "US": Rule(
        expects=("street", "postcode", "municipality", "region"),
        requires=("postcode", "municipality", "region"),
        postcode=r"(\d{5})(\d{4})?",
        postcode_joins="-",
        postcode_example="20500",
        calls={"postcode": "zip", "region": "state"},
        order=("street", "municipality region postcode", "country"),
        note=(
            "The state is part of the address rather than an optional refinement. The guide "
            "lists the lines an address is made up of -- the street, or a PO box, a rural "
            "route, a highway or poste restante, then the locality, the state and the "
            "postcode -- and marks only the line above them optional. Every one of those "
            "alternatives goes in the same box as a street, so nothing is required of it. "
            "The guide writes all nine digits; the first five alone are the ZIP code most "
            "people know their address by, and they are accepted as they always were here."
        ),
        source=upu("usa", "06/2010"),
    ),
    "CA": Rule(
        expects=("street", "postcode", "municipality", "region"),
        requires=("postcode", "municipality", "region"),
        postcode=r"([A-Z]\d[A-Z])(\d[A-Z]\d)",
        postcode_joins=" ",
        postcode_example="K1A 0A6",
        calls={"region": "province"},
        order=("street", "municipality region postcode", "country"),
        note=(
            "The guide: the municipality, the province or territory code and the postcode "
            "on one line."
        ),
        source=upu("can", "09/2014"),
    ),
    "AU": Rule(
        expects=("street", "postcode", "municipality", "region"),
        requires=("postcode", "municipality", "region"),
        postcode=r"(\d{4})",
        postcode_example="2600",
        calls={"region": "state"},
        order=("street", "municipality region postcode", "country"),
        note="The guide: the postcode to the right of the abbreviation of the state.",
        source=upu("aus", "07/2017"),
    ),
    "BR": Rule(
        expects=("street", "postcode", "municipality", "region"),
        requires=("postcode", "municipality", "region"),
        postcode=r"(\d{5})(\d{3})",
        postcode_joins="-",
        postcode_example="01310-100",
        calls={"postcode": "cep", "region": "state"},
        order=("street", "municipality region", "postcode", "country"),
        note="The guide: the state's abbreviation must follow the town's name.",
        source=upu("bra", "03/2012"),
    ),
    "JP": Rule(
        expects=("street", "postcode", "municipality", "region"),
        requires=("postcode", "municipality"),
        postcode=r"(\d{3})(\d{4})",
        postcode_joins="-",
        postcode_example="100-8111",
        calls={"region": "prefecture"},
        order=("postcode", "region municipality", "street", "country"),
        note=(
            "Japan writes largest first, which is the reverse of most of this table. The "
            "guide lets the prefecture be left out for the twenty principal cities and "
            "where it has the city's name, so it is expected and never required."
        ),
        source=upu("jpn", "06/2019"),
    ),
}


def rule_for(country: str) -> Rule:
    """The rules for a country, or the ones that ask nothing and refuse nothing."""
    return RULES.get((country or "").strip().upper(), ANYWHERE)


# ------------------------------------------------------------- a postcode, folded (#306)

#: What is taken out of a postcode before it is compared: white space of every kind, the
#: full stop, the hyphen, and what people and keyboards write in a hyphen's place -- the
#: dashes (U+2010 to U+2015), the minus sign, and the long-vowel mark a Japanese keyboard
#: gives for one. And the postal mark Japan writes before a postcode, 〒, with its two
#: other shapes (U+3020, U+3036): it says *a postcode follows* and is no part of one.
_NOT_PART_OF_IT = re.compile("[\\s.\\-‐-―−ー〒〠〶]+")

#: The letters a to z as capitals, and no other letter touched.
_CAPITALS = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)


def fold(postcode: str) -> str:
    """A postcode as it is compared: what two typings of one postcode agree on.

    Capitals, no spaces, hyphens or full stops, and the digits as ASCII whatever keyboard
    they were typed on: the full-width ones a Japanese keyboard gives, and those of any
    other script, since somebody reading Postulo in Arabic may type a Lisbon postcode in the
    digits their keyboard has. Nothing cleverer: a letter for a digit is a different code.

    Only the letters a to z are made capitals. `str.upper` turns a sharp s into two
    letters, so ``ß1 1aa`` was kept as the postcode ``SS1 1AA``; a letter that is not one
    of the twenty-six stays as it was typed, and no pattern here matches it.
    """
    text = unicodedata.normalize("NFKC", postcode or "")
    text = "".join(
        str(unicodedata.decimal(character)) if character.isdecimal() else character
        for character in text
    )
    return _NOT_PART_OF_IT.sub("", text).translate(_CAPITALS)


def written(postcode: str, country: str) -> str | None:
    """The postcode the way its country writes it, or nothing where it is not one of theirs.

    Nothing as well for a country with no row, which has no way of writing one that
    Postulo knows: the caller keeps what was typed. The country's own code in front is
    tried without, because the country is a part of its own here and several operators ask
    for the code only on post from abroad -- and Germany's asks for it never. Its ISO code,
    and the older one its row names (``D-10115``): only its own, so ``D-10115`` is no
    postcode of France's.
    """
    code = (country or "").strip().upper()
    rule = rule_for(code)
    if not rule.postcode:
        return None
    folded = fold(postcode)
    candidates = [folded]
    for prefix in (code, *rule.postcode_once):
        if prefix and folded.startswith(prefix):
            candidates.append(folded[len(prefix) :])
    for candidate in candidates:
        match = re.fullmatch(rule.postcode, candidate, flags=re.ASCII)
        if match:
            groups = [group for group in match.groups() if group]
            return rule.postcode_prefix + rule.postcode_joins.join(groups)
    return None


# ------------------------------------------------------------- the parts, as lines (#306)

#: The lines a form draws where a country has no order of its own: the ones it always drew.
PLAIN_LINES = (("street",), ("postcode", "municipality"), ("region", "country"))


def lines_for(country: str) -> tuple[tuple[str, ...], ...]:
    """The parts as the lines a form draws them on, in the order that country writes them.

    Every part is on exactly one line, which the ``order`` column alone does not promise:
    it is the order post is addressed in, and twenty-six rows leave the region out because
    post there does not name one. A form still has to offer the box -- the row may call it
    a *County* or a *Distrito* -- so a part the order does not name goes beside the country,
    where the form has always drawn the region.
    """
    order = rule_for(country).order
    if not order:
        return PLAIN_LINES
    lines = [tuple(group.split()) for group in order]
    named = {part for line in lines for part in line}
    left_out = tuple(part for part in PARTS if part not in named)
    if left_out:
        for index, line in enumerate(lines):
            if "country" in line:
                lines[index] = (*left_out, *line)
                break
        else:
            lines.append(left_out)
    return tuple(lines)
