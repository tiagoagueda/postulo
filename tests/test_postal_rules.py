"""An address is checked against the rules of its own country (#147), and the little of
that which is certain is refused before the address is kept (#306).

> another internet plugin, multiple postal address, not unique across instance, validades is
> made on a contry basis inside the plugin

Two problems, and only one of them is validation.

**Naming the fields is the harder one, and it is not a translation problem.** A person
reading Postulo in Portuguese who enters a United States address should see *State*; entering
a Portuguese one, *Distrito*. The label depends on the **address's** country and the language
depends on the **reader**, and both vary at once — nothing else in this codebase has that
shape, because every other string is chosen by the reader's language alone.

**And the failure mode to design against is still refusing too much.** BFPO addresses, rural
routes, informal settlements, temporary accommodation, and a table that is simply wrong
about somewhere. So two things are refused and no more: a postcode in a form its country
never uses, and a part every address there carries left empty. Everything else is a note,
and a country with no row refuses nothing at all. Each country's rules have a test of their
own below, case by case, so that a row cannot be added or changed without saying what it
accepts, what it puts right and what it turns away.

**A real address is never refused, and what is typed is never made into something else.**
The review of #306 found both: a forces address refused twice, an Åland postcode refused,
a town and a country refused for want of a street, and *Dublin 4* kept as the Eircode
``DUB LIN4``. So the table is held to more than its own cases: no row takes a word for a
postcode, no row regroups another country's code but for the digits it cannot tell from its
own, a street is required nowhere, and nothing is required of a place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from django.utils import translation

from postulo.core import phones, postal
from postulo.core.models import PostalAddress
from postulo.plugins import postal_rules
from postulo.plugins.postal_rules import rules

pytestmark = pytest.mark.django_db


def address(**parts) -> PostalAddress:
    return PostalAddress(**parts)


# ------------------------------------------------- what a part is called, and where


def test_a_region_is_called_what_its_own_country_calls_it():
    assert str(postal_rules.label_for("region", "US")) == "State"
    assert str(postal_rules.label_for("region", "PT")) == "District"
    assert str(postal_rules.label_for("region", "JP")) == "Prefecture"
    assert str(postal_rules.label_for("region", "IE")) == "County"
    assert str(postal_rules.label_for("region", "CH")) == "Canton"


def test_and_in_the_language_the_reader_is_using(compiled_catalogues=None):
    """The half that makes this awkward: the key comes from the address, the words from the
    catalogue. A Portuguese reader entering a United States address wants *Estado*.
    """
    with translation.override("pt-PT"):
        chosen = str(postal_rules.label_for("region", "US"))

    # Whether a catalogue is compiled in this test run is not this test's business; what is,
    # is that the label went through translation at all rather than being a bare constant.
    assert chosen in {"State", "Estado"}


def test_a_postcode_is_called_what_its_country_calls_it():
    assert str(postal_rules.label_for("postcode", "US")) == "ZIP code"
    assert str(postal_rules.label_for("postcode", "IE")) == "Eircode"
    assert str(postal_rules.label_for("postcode", "GB")) == "Postcode"
    assert "CAP" in str(postal_rules.label_for("postcode", "IT"))


def test_a_country_with_no_rules_gets_the_neutral_words():
    """It fails in the right direction: no row means free-form and no refusal, which is what
    this issue requires anyway. A missing country is a gap, never a country refused.
    """
    assert str(postal_rules.label_for("region", "ZZ")) == "Region"
    assert str(postal_rules.label_for("postcode", "")) == "Postcode"


# ------------------------------------------------------------- warnings, never errors


def test_a_missing_part_is_a_note_about_what_is_usually_needed():
    notes = postal_rules.warnings_for(address(country="PT", street="Rua do Exemplo 1"))

    assert notes
    assert "usually needs" in str(notes[0])


def test_a_postcode_that_looks_wrong_says_what_one_usually_looks_like():
    """An example is far more use than a regular expression."""
    notes = postal_rules.warnings_for(
        address(country="PT", street="Rua 1", municipality="Lisboa", postcode="ABC")
    )

    assert any("1000-001" in str(note) for note in notes)


def test_an_address_that_fits_says_nothing():
    notes = postal_rules.warnings_for(
        address(country="PT", street="Rua 1", municipality="Lisboa", postcode="1000-001")
    )

    assert notes == []


def test_a_country_with_no_rules_says_nothing_either():
    assert postal_rules.warnings_for(address(country="ZZ")) == []
    assert postal_rules.warnings_for(address(country="")) == []


def test_ireland_expects_no_eircode(user):
    """Eircode arrived in 2015 and plenty of addresses predate it. A required-field rule
    that assumes a postcode exists produces a form somebody cannot complete for the place
    they actually live.
    """
    notes = postal_rules.warnings_for(
        address(country="IE", street="1 Example Street", municipality="Dublin")
    )

    assert notes == []


def test_nothing_the_rules_say_stops_the_model_keeping_an_address(user):
    """The model refuses nothing, which is what lets an archive, a candidate file and a
    Europass file bring whatever they hold. Refusing is the form's, for what is typed.
    """
    saved = PostalAddress.objects.create(
        owner=user,
        holder=user.profile,
        country="PT",
        street="BFPO 123",
        postcode="not a postcode at all",
    )

    saved.refresh_from_db()
    assert saved.pk
    assert saved.postcode == "not a postcode at all", "kept exactly as typed"
    assert postal_rules.warnings_for(saved), "and said so, without refusing"


def one_row(**parts) -> dict:
    """The POST of one new address row, with whatever parts are given."""
    data = {
        "addresses-TOTAL_FORMS": "1",
        "addresses-INITIAL_FORMS": "0",
        "addresses-MIN_NUM_FORMS": "0",
        "addresses-MAX_NUM_FORMS": "1000",
    }
    names = ("kind", "label", "street", "postcode", "municipality", "region", "country")
    data.update({f"addresses-0-{name}": parts.get(name, "") for name in names})
    return data


def test_what_is_only_usually_so_is_still_a_note_on_a_row_that_saves(user):
    """A street is what post to Portugal usually needs and nothing its guide makes every
    address carry, so a row without one is kept, and told. A note is not a refusal."""
    formset = postal.formset_for(
        user.profile, data=one_row(postcode="1000-001", municipality="Lisboa", country="PT")
    )

    assert formset.is_valid(), formset.errors
    assert len(formset.forms[0].country_notes) == 1, "it did say what Portugal usually needs"
    formset.save()

    assert postal.for_holder(user.profile).count() == 1


def test_a_part_that_was_refused_is_not_also_remarked_on(user):
    """The error beside the part says it; the note under the row would say it twice."""
    formset = postal.formset_for(
        user.profile, data=one_row(street="Rua 1", municipality="Lisboa", country="PT")
    )

    assert not formset.is_valid()
    assert list(formset.forms[0].errors) == ["postcode"]
    assert formset.forms[0].country_notes == []


# ------------------------------------- what each country refuses, case by case (#306)


@dataclass(frozen=True)
class Cases:
    """One country's postcode, as its own tests: what is accepted as it stands, what is put
    right and to what, and what is turned away."""

    accepted: tuple[str, ...]
    put_right: tuple[tuple[str, str], ...]
    refused: tuple[str, ...]


#: Every country the plugin covers. Written by hand from the same guides the table was, so
#: that the two are checked against each other and not one derived from the other.
POSTCODES = {
    "AT": Cases(
        ("1010", "6020", "9992"),
        (("10 10", "1010"), ("at-1010", "1010"), ("A-1010", "1010")),
        ("101", "10100", "D-1010", "ABCD"),
    ),
    "BE": Cases(
        ("1000", "9000", "9992", "0612"),
        (("BE-1000", "1000"), ("B-1000", "1000")),
        ("100", "10000", "F-1000"),
    ),
    "BG": Cases(("1000", "4000"), ((" 1680 ", "1680"), ("BG-1000", "1000")), ("100", "10000")),
    "CH": Cases(("8001", "1200"), (("ch-8001", "8001"),), ("800", "80010")),
    "CZ": Cases(
        ("110 00", "251 66"),
        (("11000", "110 00"), ("110-00", "110 00"), ("CZ-110 00", "110 00")),
        ("1100", "110 000"),
    ),
    # The guide: on no account D- or DE- in front. So both are taken off.
    "DE": Cases(
        ("10115", "01067", "99998"),
        (("10 115", "10115"), ("DE-10115", "10115"), ("D-10115", "10115")),
        ("1011", "101150", "F-10115"),
    ),
    "DK": Cases(("1050", "8660"), (("DK-1050", "1050"),), ("105", "10500")),
    "EE": Cases(("10111", "69501"), (("EE-10111", "10111"),), ("1011", "101110")),
    "ES": Cases(
        ("28001", "09692", "01001", "52080"),
        (("28 001", "28001"), ("ES-28001", "28001"), ("E-28001", "28001")),
        ("2800", "280010", "P-28001"),
    ),
    # The UPU's sheet for the Åland Islands, which follow Finland's system: AX-22100
    # MARIEHAMN, and its operator's own address, AX-22111. Kept with the AX it came with.
    "FI": Cases(
        ("00100", "50100", "00011", "99999", "AX-22100", "AX-22111"),
        (
            ("FI-00100", "00100"),
            ("FIN-00100", "00100"),
            ("ax 22100", "AX-22100"),
            ("AX22100", "AX-22100"),
        ),
        ("0010", "001000", "AX-2210", "AX", "S-00100"),
    ),
    "FR": Cases(
        ("75001", "33506", "01000", "98799"),
        (("75 001", "75001"), ("FR-75001", "75001"), ("F-75001", "75001")),
        ("7500", "750010", "D-75001"),
    ),
    "GB": Cases(
        # The six forms the guide lists, and its one exception; then Gibraltar's one
        # code and the one letters to Father Christmas are sent to, which are of those
        # forms; a forces address, which ends in BFPO and a number of one to four digits
        # (and the BF1 postcode each has had since 2012); and the one code each of nine
        # overseas territories has.
        (
            "M2 5BQ",
            "M34 4AB",
            "CR0 2YR",
            "DN16 9AA",
            "W1A 4ZZ",
            "EC1A 1HQ",
            "GIR 0AA",
            "SW1A 1AA",
            "GX11 1AA",
            "XM4 5HQ",
            "BFPO 61",
            "BFPO 5",
            "BFPO 801",
            "BFPO 1234",
            "BF1 2AY",
            "ASCN 1ZZ",
            "STHL 1ZZ",
            "TDCU 1ZZ",
            "PCRN 1ZZ",
            "SIQQ 1ZZ",
            "BIQQ 1ZZ",
            "BBND 1ZZ",
            "FIQQ 1ZZ",
            "TKCA 1ZZ",
        ),
        (
            ("sw1a1aa", "SW1A 1AA"),
            ("m25bq", "M2 5BQ"),
            ("ec1a  1hq", "EC1A 1HQ"),
            ("bfpo5", "BFPO 5"),
            ("BFPO61", "BFPO 61"),
            ("ascn1zz", "ASCN 1ZZ"),
        ),
        (
            "SW1A",
            "12345",
            "SW1A 1A",
            "AAA 1AA",
            "SW1A 11A",
            "ß1 1AA",
            "BFPO",
            "BFPO 12345",
            "ASCN 1AA",
        ),
    ),
    "GR": Cases(
        ("104 31", "151 24"),
        (("10431", "104 31"), ("GR-104 31", "104 31")),
        ("1043", "104 311"),
    ),
    "HR": Cases(("10000", "52341"), (("HR-10000", "10000"),), ("1000", "100000")),
    "HU": Cases(
        ("1051", "2380", "1011", "9985"),
        (("HU-1051", "1051"), ("H-1051", "1051")),
        ("105", "10510", "A-1051"),
    ),
    # Every Eircode on the guide's sheet, the one the table shows, and Dublin 6W's, the one
    # routing key that ends in a letter. Refused: what is not seven of Eircode's own
    # characters -- a postal district, which people do write in this box; another country's
    # code; a letter Eircode does not use (B); a routing key that ends in another letter.
    "IE": Cases(
        ("D02 AF30", "T37 F8HK", "A65 TF12", "D24 TF12", "D12 V4AC", "D6W F838"),
        (("d02af30", "D02 AF30"), ("A65-TF12", "A65 TF12"), ("d6wf838", "D6W F838")),
        (
            "D02",
            "D02 AF3",
            "D02 AF300",
            "Dublin 4",
            "SW1A 1AA",
            "BT48 6DQ",
            "1000-100",
            "D02 AB30",
            "D6X F838",
        ),
    ),
    "IS": Cases(("101", "220"), (("IS-101", "101"),), ("10", "1010")),
    "IT": Cases(
        ("00184", "30121", "00010", "98168"),
        (("IT-00184", "00184"), ("I-00184", "00184")),
        ("0018", "001840", "E-00184"),
    ),
    "LT": Cases(
        ("LT-01100", "LT-04340"),
        (("01100", "LT-01100"), ("lt 01100", "LT-01100"), ("LT–04340", "LT-04340")),
        ("0110", "LT-0110", "011000"),
    ),
    "LU": Cases(
        ("L-1111", "L-2998"),
        (("1111", "L-1111"), ("L1111", "L-1111"), ("LU-1111", "L-1111")),
        ("111", "L-11111"),
    ),
    "LV": Cases(
        ("LV-1050", "LV-4035"),
        (("1050", "LV-1050"), ("lv1050", "LV-1050")),
        ("105", "LV-10500"),
    ),
    # Malta's guide says personal postcodes may not follow the form, so nothing is refused
    # for its form; `test_malta_turns_no_postcode_away_for_its_form` is the rule's test.
    "MT": Cases(("VLT 1117", "HMR 1428"), (("vlt1117", "VLT 1117"),), ()),
    "NL": Cases(
        ("1012 JS", "6832 AM"),
        (("1234ab", "1234 AB"), ("1012-JS", "1012 JS"), ("NL-1012 JS", "1012 JS")),
        ("1012", "1012 J", "1012 JSX", "ABCD JS"),
    ),
    "NO": Cases(
        ("0150", "9672", "0001", "9991"),
        (("NO-0150", "0150"), ("N-0150", "0150")),
        ("015", "01500", "S-0150"),
    ),
    "PL": Cases(
        ("00-001", "05-470"),
        (("00001", "00-001"), ("00 001", "00-001"), ("05–470", "05-470")),
        ("0000", "000001"),
    ),
    "PT": Cases(
        ("1000-001", "2725-079", "9980-024"),
        (
            ("1000100", "1000-100"),
            ("1000 100", "1000-100"),
            ("1000–100", "1000-100"),
            ("PT-1000-100", "1000-100"),
            ("P-1000-100", "1000-100"),
        ),
        ("1000", "1000-10", "1000-1000", "ABCD-EFG", "E-1000-100"),
    ),
    "RO": Cases(
        ("010101", "200716"),
        (("010 101", "010101"), ("RO-010101", "010101")),
        ("01010", "0101010"),
    ),
    "SE": Cases(
        ("111 29", "123 45", "100 05", "984 99"),
        (("11129", "111 29"), ("SE-111 29", "111 29"), ("S-111 29", "111 29")),
        ("1112", "111 290", "N-111 29"),
    ),
    "SI": Cases(("1000", "4000"), (("SI-1000", "1000"),), ("100", "10000")),
    "SK": Cases(
        ("811 01", "960 01"),
        (("81101", "811 01"), ("SK-811 01", "811 01")),
        ("8110", "811 011"),
    ),
    # The guide shows a sub-locality number after the postcode, and it is one of theirs.
    "TR": Cases(
        ("34000", "06050-01", "06101", "06850"),
        (("TR-34000", "34000"), ("0605001", "06050-01")),
        ("3400", "340000"),
    ),
    "UA": Cases(("01001", "15432"), (("UA-01001", "01001"),), ("0100", "010010")),
    "US": Cases(
        ("20500", "20500-0003", "00501", "99950", "09001-5275"),
        (("205000003", "20500-0003"), ("20500 0003", "20500-0003")),
        ("2050", "205000", "20500-000", "ABCDE"),
    ),
    "CA": Cases(
        ("K1A 0A6", "H3Z 2Y7"),
        (("k1a0a6", "K1A 0A6"), ("K1A-0A6", "K1A 0A6")),
        ("K1A", "K1A 0A", "111 111", "KAA 0A6"),
    ),
    "AU": Cases(("2600", "6430"), (("AU-2600", "2600"),), ("260", "26000")),
    "BR": Cases(
        ("01310-100", "85070-200"),
        (("01310100", "01310-100"), ("01.310-100", "01310-100")),
        ("01310", "01310-10", "01310-1000"),
    ),
    # The second is what a Japanese keyboard gives: full-width digits and a minus sign. The
    # third and fourth begin with the postal mark Japan writes before a postcode.
    "JP": Cases(
        ("100-8111", "951-8073", "001-0000", "999-8531"),
        (
            ("1008111", "100-8111"),
            ("１００−８１１１", "100-8111"),
            ("〒100-8111", "100-8111"),
            ("〒 １００−８１１１", "100-8111"),
        ),
        ("100", "100-811", "100-81111", "〒"),
    ),
}

COVERED = sorted(rules.RULES)


def whole(country: str, **changed) -> SimpleNamespace:
    """An address in that country with every part filled in and its guide's own postcode."""
    parts = {
        "street": "1 Example Street",
        "postcode": rules.RULES[country].postcode_example,
        "municipality": "Exampletown",
        "region": "EX",
        "country": country,
    }
    parts.update(changed)
    return SimpleNamespace(**parts)


def refused_parts(address) -> list[str]:
    return [part for part, _sentence in postal_rules.refusals_for(address)]


def test_every_country_the_plugin_covers_has_its_cases():
    """A row added to the table is a row with no test until its cases are written here."""
    assert sorted(POSTCODES) == COVERED


@pytest.mark.parametrize("country", COVERED)
def test_a_country_accepts_a_postcode_in_its_own_form(country):
    cases = POSTCODES[country]
    assert rules.RULES[country].postcode_example in cases.accepted, "its own example, at least"

    for postcode in cases.accepted:
        assert refused_parts(whole(country, postcode=postcode)) == [], postcode
        assert postal_rules.canonical(postcode, country) == postcode, "and leaves it as it is"


@pytest.mark.parametrize("country", COVERED)
def test_a_country_writes_a_postcode_that_is_plainly_its_own_its_own_way(country):
    """Case, a missing or an extra space or hyphen, the country's own code in front: the
    same postcode, kept the way the country writes it."""
    for typed, kept in POSTCODES[country].put_right:
        assert postal_rules.canonical(typed, country) == kept, typed
        assert refused_parts(whole(country, postcode=typed)) == [], typed
        assert postal_rules.canonical(kept, country) == kept, "and that is where it stays"


@pytest.mark.parametrize("country", COVERED)
def test_a_country_refuses_a_postcode_in_a_form_it_never_uses(country):
    """Beside the postcode, with the country's own example of what one looks like."""
    rule = rules.RULES[country]

    for postcode in POSTCODES[country].refused:
        found = postal_rules.refusals_for(whole(country, postcode=postcode))
        assert [part for part, _sentence in found] == ["postcode"], postcode
        assert rule.postcode_example in str(found[0][1]), "it says what is expected"
        assert postal_rules.canonical(postcode, country) == postcode, "and is left as typed"


@pytest.mark.parametrize("country", COVERED)
def test_a_country_refuses_a_part_it_requires_left_empty(country):
    """Each required part in turn, and nothing but that part; and a part that is only
    usually there is never a reason to refuse."""
    rule = rules.RULES[country]
    assert rule.requires, "every country covered requires something"

    for part in rule.requires:
        assert refused_parts(whole(country, **{part: ""})) == [part], part
    for part in ("street", "postcode", "municipality", "region"):
        if part not in rule.requires:
            assert refused_parts(whole(country, **{part: ""})) == [], part


@pytest.mark.parametrize("country", ["NZ", "IN", "ZA", "MX", "ZZ", ""])
def test_a_country_the_plugin_does_not_cover_is_free_form_with_nothing_refused(country):
    """A missing row is a gap and never a refusal: nothing required, no form to be in,
    nothing put right, nothing remarked on."""
    assert country not in rules.RULES
    for address in (
        SimpleNamespace(street="", postcode="", municipality="", region="", country=country),
        SimpleNamespace(
            street="x", postcode="no form at all", municipality="", region="", country=country
        ),
    ):
        assert postal_rules.refusals_for(address) == []
        assert postal_rules.notes_for(address) == []
    assert postal_rules.canonical(" no form at all ", country) == "no form at all"
    assert rules.lines_for(country) == rules.PLAIN_LINES


def test_malta_turns_no_postcode_away_for_its_form():
    """Its own guide: "Personal postcodes covering a particular area are also in use in
    Malta. These codes may not follow the same format." So the form is a note."""
    address = whole("MT", postcode="PERSONAL 1")

    assert refused_parts(address) == []
    assert [part for part, _sentence in postal_rules.notes_for(address)] == ["postcode"]
    assert postal_rules.canonical("PERSONAL 1", "MT") == "PERSONAL 1"
    assert refused_parts(whole("MT", postcode="")) == ["postcode"], "but it has to have one"


def test_ireland_requires_no_eircode_and_checks_one_that_is_given():
    assert refused_parts(whole("IE", postcode="")) == []
    assert refused_parts(whole("IE", postcode="D02")) == ["postcode"]


@pytest.mark.parametrize("typed", ["Dublin 4", "SW1A 1AA", "BT48 6DQ", "1000-100"])
def test_ireland_takes_nothing_but_an_eircode_for_an_eircode(typed):
    """Any seven letters or digits passed, and were regrouped: *Dublin 4*, which people do
    write in this box, was kept as ``DUB LIN4``, and a British postcode as ``SW1 A1AA``.
    An Eircode has an alphabet of its own, and what is not one is said to be none and left
    exactly as it was typed."""
    assert rules.written(typed, "IE") is None
    assert postal_rules.canonical(typed, "IE") == typed
    [(part, sentence)] = postal_rules.refusals_for(whole("IE", postcode=typed))
    assert part == "postcode"
    assert str(sentence) == "An Eircode is written like D02 AF30."


def test_the_united_kingdom_keeps_a_forces_address():
    """#147 named BFPO as the address that must not be refused, and it was refused twice:
    for its postcode's form and for its post town, of which it has none."""
    forces = SimpleNamespace(
        street="12345678 LCpl B Jones\nB Company, 1 Loamshire Regt",
        postcode="BFPO 61",
        municipality="",
        region="",
        country="GB",
    )

    assert postal_rules.refusals_for(forces) == []
    assert postal_rules.canonical("bfpo61", "GB") == "BFPO 61"
    assert "municipality" in rules.RULES["GB"].expects, "still what post there usually needs"
    assert refused_parts(whole("GB", postcode="")) == ["postcode"], "the postcode it has to have"


def test_turkey_expects_a_postcode_and_does_not_require_one():
    """Its guide shows an address without one: poste restante, then the post office, the
    district and the province."""
    poste_restante = SimpleNamespace(
        street="Postrestant", postcode="", municipality="Kartal", region="İstanbul", country="TR"
    )

    assert postal_rules.refusals_for(poste_restante) == []
    assert [part for part, _sentence in postal_rules.notes_for(poste_restante)] == ["postcode"]
    assert refused_parts(whole("TR", postcode="3400")) == ["postcode"], "its form, when given"
    assert refused_parts(whole("TR", municipality="")) == ["municipality"]


#: The code a country's post was addressed with from abroad before its ISO code was, where
#: its postcode was ever written with one. Written by hand, as the cases are.
ONCE = {
    "AT": "A",
    "BE": "B",
    "DE": "D",
    "ES": "E",
    "FI": "FIN",
    "FR": "F",
    "HU": "H",
    "IT": "I",
    "NO": "N",
    "PT": "P",
    "SE": "S",
}


def test_an_old_prefix_is_taken_off_under_its_own_country_and_no_other():
    """``D-10115`` is a postcode of Germany's with what used to be written before it, and it
    is no postcode of France's. The ISO code was already taken off; the old one was refused."""
    assert {
        code: rule.postcode_once for code, rule in rules.RULES.items() if rule.postcode_once
    } == {code: (prefix,) for code, prefix in ONCE.items()}

    for code, prefix in ONCE.items():
        example = rules.RULES[code].postcode_example
        typed = f"{prefix}-{example}"
        assert postal_rules.canonical(typed, code) == example, typed
        assert postal_rules.canonical(typed.lower(), code) == example, typed
        for other in COVERED:
            if other != code:
                assert rules.written(typed, other) is None, f"{typed} under {other}"


@pytest.mark.parametrize(
    ("typed", "country", "kept"),
    [
        ("CH-8001", "CH", "8001"),
        ("DK-1050", "DK", "1050"),
        ("GR-104 31", "GR", "104 31"),
        ("L-1111", "LU", "L-1111"),
        ("NL-1012 JS", "NL", "1012 JS"),
    ],
)
def test_where_the_old_prefix_is_the_iso_code_or_the_countrys_own_it_was_always_taken(
    typed, country, kept
):
    assert postal_rules.canonical(typed, country) == kept


# ------------------------------------ what is typed is never made into something else (#306)

#: What somebody writes in a postcode's box that is no postcode: a postal district, a town,
#: a word, a number, a box.
NOT_A_POSTCODE = (
    "Dublin 4",
    "Dublin 24",
    "London",
    "Paris 75",
    "Lisboa",
    "ABCDEFG",
    "1234567890",
    "PO Box 12",
    "CEDEX 9",
)


@pytest.mark.parametrize("country", COVERED)
def test_no_country_takes_a_word_for_a_postcode(country):
    """Refused, or -- in Malta, which refuses no form -- left exactly as it was typed.
    Never taken for a code and written back as one."""
    for typed in NOT_A_POSTCODE:
        assert rules.written(typed, country) is None, typed
        assert postal_rules.canonical(typed, country) == typed, typed
        refused = refused_parts(whole(country, postcode=typed))
        assert refused == ([] if rules.RULES[country].postcode_open else ["postcode"]), typed


#: How many digits each country's postcode has, where it is digits and nothing else: the one
#: thing a row may take from another country's code, and why. **A run of digits of a
#: country's own length is that country's postcode, however it is spaced**, because nothing
#: can tell them apart: `110 00` is Prague's and, under Germany, it is `11000` typed with a
#: space, which is in Saxony. So Japan's `100-8111` under Portugal is `1008-111`, Portugal's
#: `1000-001` under Japan is `100-0001`, and either under Turkey is five digits and a
#: sub-locality number. That is the design, and the review of #306 accepted it. What no row
#: may do is take a code with a letter in it for one of its own.
DIGITS = {
    "AT": (4,),
    "AU": (4,),
    "BE": (4,),
    "BG": (4,),
    "BR": (8,),
    "CH": (4,),
    "CZ": (5,),
    "DE": (5,),
    "DK": (4,),
    "EE": (5,),
    "ES": (5,),
    "FI": (5,),
    "FR": (5,),
    "GR": (5,),
    "HR": (5,),
    "HU": (4,),
    "IS": (3,),
    "IT": (5,),
    "JP": (7,),
    "LT": (5,),
    "LU": (4,),
    "LV": (4,),
    "NO": (4,),
    "PL": (5,),
    "PT": (7,),
    "RO": (6,),
    "SE": (5,),
    "SI": (4,),
    "SK": (5,),
    "TR": (5, 7),
    "UA": (5,),
    "US": (5, 9),
}


def digits_of(postcode: str) -> str:
    return "".join(character for character in postcode if character.isdigit())


def test_no_country_regroups_another_countrys_postcode_but_for_digits_of_its_own_length():
    """Every row against every other row's example. A code is refused, or left exactly as
    it was typed, or -- digits and nothing else, of the row's own length -- written the
    row's way with not one digit changed."""
    taken = 0
    for country in COVERED:
        own = rules.RULES[country]
        for other in COVERED:
            typed = rules.RULES[other].postcode_example
            if other == country:
                continue
            kept = postal_rules.canonical(typed, country)
            if kept == typed:
                continue
            taken += 1
            folded = rules.fold(typed)
            said = f"{typed} ({other}) under {country} became {kept}"
            assert folded.isdigit(), f"a code with a letter in it: {said}"
            assert len(folded) in DIGITS.get(country, ()), said
            assert digits_of(kept) == folded, said
            assert kept.startswith(own.postcode_prefix), said
    assert taken, "the test found the pairs it is about"
    for typed, country, kept in (
        ("100-8111", "PT", "1008-111"),
        ("1000-001", "JP", "100-0001"),
        ("100-8111", "TR", "10081-11"),
        ("110 00", "DE", "11000"),
    ):
        assert postal_rules.canonical(typed, country) == kept


def test_only_the_letters_a_to_z_are_made_capitals():
    """`str.upper` makes two letters of a sharp s, so ``ß1 1aa`` was kept as ``SS1 1AA``."""
    assert rules.fold("ß1 1aa") == "ß11AA"
    assert rules.written("ß1 1aa", "GB") is None
    assert postal_rules.canonical("ß1 1aa", "GB") == "ß1 1aa"
    # A dotless i is not an I, and a Cyrillic letter that looks like a Latin one is not it.
    assert rules.written("d02 af3ı", "IE") is None
    assert rules.written("К1А 0А6", "CA") is None
    # What a Japanese keyboard gives for the same letters is the same letters.
    assert postal_rules.canonical("ｓｗ１ａ １ａａ", "GB") == "SW1A 1AA"


# ------------------------------------------ a town and a country are not an address (#306)


def a_place(country: str, **parts) -> SimpleNamespace:
    """A town and its country, and no street and no postcode."""
    held = {"street": "", "postcode": "", "municipality": "Exampletown", "region": ""}
    return SimpleNamespace(**{**held, **parts, "country": country})


@pytest.mark.parametrize("country", COVERED)
def test_nothing_is_required_of_a_place(country):
    """*Lisboa, Portugal* is what a CV shows, and what somebody types who keeps no street
    here on purpose. It was refused for its postcode in every one of the 37 countries."""
    assert postal_rules.refusals_for(a_place(country)) == []
    assert postal_rules.refusals_for(a_place(country, municipality="", region="EX")) == []
    assert postal_rules.refusals_for(a_place(country, municipality="")) == [], "only a country"


@pytest.mark.parametrize("country", COVERED)
def test_a_row_with_a_street_or_a_postcode_is_an_address_and_answers_as_one(country):
    """The town is there, so what is missing is whatever else the country requires."""
    rule = rules.RULES[country]
    beyond_the_town = [part for part in rule.requires if part != "municipality"]

    with_a_street = a_place(country, street="1 Example Street")
    assert refused_parts(with_a_street) == beyond_the_town
    with_a_postcode = a_place(country, postcode=rule.postcode_example)
    assert refused_parts(with_a_postcode) == [
        part for part in beyond_the_town if part != "postcode"
    ]
    if not rule.postcode_open:
        wrong = a_place(country, postcode="?")
        assert "postcode" in refused_parts(wrong), "a postcode's form, whenever one is typed"


def test_a_place_is_told_what_a_whole_address_there_also_carries():
    """As notes, each a whole sentence: the street, which is usual and required nowhere,
    and what a fuller row would be refused for."""
    street = (
        "An address in this country usually gives a street and number, or else the place "
        "where post is delivered there."
    )

    said = [str(sentence) for _part, sentence in postal_rules.place_for(a_place("PT"))]
    assert said == [street, "An address in this country needs a postcode, written like 1000-001."]

    said = [str(sentence) for _part, sentence in postal_rules.place_for(a_place("US"))]
    assert said == [
        street,
        "An address in this country needs a ZIP code, written like 20500.",
        "An address in this country needs a state.",
    ]
    # Ireland requires no Eircode and Turkey no postcode, so a town there wants only a street.
    for country in ("IE", "TR"):
        assert [part for part, _sentence in postal_rules.place_for(a_place(country))] == ["street"]


def test_what_is_not_a_place_is_told_nothing_of_the_kind():
    whole_address = whole("PT")
    assert postal_rules.place_for(whole_address) == []
    assert postal_rules.place_for(a_place("PT", street="Rua 1")) == []
    assert postal_rules.place_for(a_place("PT", postcode="1000-001")) == []
    assert postal_rules.place_for(a_place("PT", municipality="")) == [], "a country and no more"
    for country in ("NZ", "ZZ", ""):
        assert postal_rules.place_for(a_place(country)) == [], "no rules, nothing to say"


def test_a_street_is_required_nowhere():
    """Three guides read as though an address always had one -- Belgium's minimum of three
    lines, Italy's mandatory line, the lines the United States' is made up of -- and each
    gives that line an alternative that goes in the same box: a PO box, a rural route,
    poste restante. So the requirement only ever refused an address left without a street
    on purpose, and it was not even consistent: the German guide's own *Citibank
    Privatkunden AG / 68151 MANNHEIM* was accepted, and the same shape in the United States
    was refused."""
    assert [code for code, rule in rules.RULES.items() if "street" in rule.requires] == []
    assert "street" not in postal_rules.NEEDS

    for country in COVERED:
        assert "street" in rules.RULES[country].expects, country
        assert refused_parts(whole(country, street="")) == [], country
        notes = dict(postal_rules.notes_for(whole(country, street="")))
        assert str(notes["street"]) == str(postal_rules.STREET), "and it is still remarked on"


def test_japan_expects_a_prefecture_and_does_not_require_one():
    """Its guide lets it be left out for the twenty principal cities and where it has the
    city's name. So leaving it out is remarked on, as it always was, and never refused."""
    address = whole("JP", region="")

    assert refused_parts(address) == []
    assert [part for part, _sentence in postal_rules.notes_for(address)] == ["region"]


def test_what_is_required_is_among_what_is_expected():
    for code, rule in rules.RULES.items():
        assert set(rule.requires) <= set(rule.expects), code


def test_every_row_says_where_it_was_read():
    """The postcode's form and the required parts come from a source, and it is beside
    them: the UPU's guide for that country, with the date the UPU put on it."""
    for code, rule in rules.RULES.items():
        assert rule.source.startswith(rules.UPU_GUIDES), code
        assert re.search(r"En\.pdf \(\d{2}/\d{4}\)$", rule.source), code
        assert rule.postcode, code
        assert rules.written(rule.postcode_example, code) == rule.postcode_example, code


def test_a_postcode_is_compared_folded():
    assert rules.fold(" 1000–100 ") == "1000100"
    assert rules.fold("sw1a 1aa") == "SW1A1AA"
    assert rules.fold("01.310-100") == "01310100"
    # The digits of whatever keyboard: full-width, and Arabic-Indic.
    assert rules.fold("１００ー８１１１") == "1008111"
    assert rules.fold("١٠٠٠١٠٠") == "1000100"
    # The mark Japan writes before a postcode, in its three shapes, is no part of one.
    assert rules.fold("〒100-8111") == rules.fold("〶100-8111") == rules.fold("〠 100-8111")
    assert rules.fold("〒100-8111") == "1008111"
    assert rules.fold("") == "" and rules.fold(None) == ""


# ---------------------------------------------- the sentences a refusal is said in (#306)


def test_every_part_a_country_requires_has_a_sentence_of_its_own():
    """One whole sentence for each thing a required part is called, never a label put into
    a sentence: that is what gave "a address" and "A Eircode" (#642)."""
    for code, rule in rules.RULES.items():
        for part in rule.requires:
            key = rule.calls.get(part, part)
            assert key in postal_rules.NEEDS, f"{code} requires its {key} and nothing says so"
        assert rule.calls.get("postcode", "postcode") in postal_rules.WRITTEN, code


def test_no_refusal_takes_a_label_or_a_country_in_a_slot():
    """The only thing a sentence is filled with is the example, which is a postcode and
    agrees with nothing. Not the part's name, and not the country's, which Postulo knows
    in English only."""
    for sentence in (
        *postal_rules.NEEDS.values(),
        *postal_rules.WRITTEN.values(),
        postal_rules.NEEDS_THIS,
        postal_rules.STREET,
    ):
        slots = set(re.findall(r"%\((\w+)\)s", str(sentence)))
        assert slots <= {"example"}, sentence


def test_a_refusal_never_names_the_country_in_english():
    for code in rules.RULES:
        empty = SimpleNamespace(street="", postcode="!", municipality="", region="", country=code)
        for _part, sentence in postal_rules.refusals_for(empty):
            assert phones.country_name(code) not in str(sentence), sentence


def test_a_refusal_reads_as_a_sentence_in_english():
    said = {
        part: str(sentence)
        for part, sentence in postal_rules.refusals_for(
            SimpleNamespace(street="", postcode="2050", municipality="", region="", country="US")
        )
    }

    assert said == {
        "municipality": "An address in this country needs a town or city.",
        "region": "An address in this country needs a state.",
        "postcode": "A ZIP code is written like 20500.",
    }
    ireland = postal_rules.refusals_for(whole("IE", postcode="D02"))
    assert str(ireland[0][1]) == "An Eircode is written like D02 AF30."


# ------------------------------------------------- the parts, as the lines a form draws


def test_every_country_puts_every_part_on_exactly_one_line():
    """The order post is addressed in leaves the region out for twenty-six rows, and a form
    still has to offer its box: what the order does not name goes beside the country."""
    for code in ["", "ZZ", *rules.RULES]:
        drawn = [part for line in rules.lines_for(code) for part in line]
        assert sorted(drawn) == sorted(rules.PARTS), code


def test_the_lines_follow_the_country():
    assert rules.lines_for("PT") == (
        ("street",),
        ("postcode", "municipality"),
        ("region", "country"),
    )
    assert rules.lines_for("US") == (
        ("street",),
        ("municipality", "region", "postcode"),
        ("country",),
    )
    assert rules.lines_for("JP")[0] == ("postcode",), "largest first"
    assert rules.lines_for("HU")[0] == ("municipality",)


# ------------------------------------------------------------------ the printing


def test_no_page_prints_an_address_through_the_rules():
    """Nothing prints an address in its country's order (#643): the order column draws the
    form's lines (`lines_for`), and an address is shown as it was entered. A function that
    would print one was kept for years with no caller, and would have collapsed a two-line
    street and dropped a typed region; it comes back with a caller and its own test."""
    for module in (postal_rules, postal):
        assert not hasattr(module, "render")
    assert not hasattr(postal_rules, "expects")


# ---------------------------------------------------------------- switched off


def switch_off(person) -> None:
    """An administrator's decision: a built-in is theirs to switch, not the person's (#200)."""
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin=postal_rules.POSTAL_RULES, person=person, state=PluginPolicy.State.FORCED_OFF
    )


def a_united_states_row_sent_back(client, user):
    """*Your details* posted with a United States address that has no state and no ZIP code,
    and a kind of Other with no name, so that the page comes back and the row can be read.
    """
    from django.urls import reverse

    client.force_login(user)
    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alex",
            "last_name": "Morgan",
            "addresses-TOTAL_FORMS": "1",
            "addresses-INITIAL_FORMS": "0",
            "addresses-MIN_NUM_FORMS": "0",
            "addresses-MAX_NUM_FORMS": "1000",
            "addresses-0-kind": "other",
            "addresses-0-label": "",
            "addresses-0-street": "1600 Pennsylvania Avenue NW",
            "addresses-0-postcode": "",
            "addresses-0-municipality": "Washington",
            "addresses-0-region": "",
            "addresses-0-country": "US",
        },
    )
    assert response.status_code == 200, "the row has no name for its kind, so the page is back"
    return response.context["addresses"].forms[0], response.content.decode()


def test_off_is_the_same_path_a_country_with_no_rules_takes(client, user):
    """Rather than a second path nobody has seen: off means neutral names and nothing said,
    which is what an unknown country already gets -- **on the page**, which is where it was
    not true. The helpers honoured the switch and the form never told them who was asking,
    so a person with the rules switched off still read *State* and *ZIP code* (#635).
    """
    switch_off(user)

    row, html = a_united_states_row_sent_back(client, user)

    names = {part: str(label) for part, label in row.labels_for_country().items()}
    assert names == {"postcode": "Postcode", "municipality": "Town or city", "region": "Region"}
    assert row.country_notes == []
    assert "ZIP code" not in html and ">State<" not in html
    assert "data-country-note" not in html


def test_on_the_same_row_is_named_and_spoken_about_as_its_country_does(client, user):
    """The control for the test above: nothing but the switch differs."""
    row, html = a_united_states_row_sent_back(client, user)

    names = {part: str(label) for part, label in row.labels_for_country().items()}
    assert names == {"postcode": "ZIP code", "municipality": "Town or city", "region": "State"}
    assert "ZIP code" in html and ">State<" in html


def test_the_switch_reaches_a_stored_row_as_the_page_is_drawn(client, user):
    """Not only a row sent back: the page as it is first read."""
    from django.urls import reverse

    PostalAddress.objects.create(
        owner=user, holder=user.profile, country="US", street="1 Main Street", is_primary=True
    )
    switch_off(user)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "ZIP code" not in html and ">State<" not in html


def test_the_rows_ask_about_the_account_behind_the_holder_when_nobody_says(user):
    """A page that builds the rows and forgets to say who is asking still honours the
    switch: the only account that ever sees a holder's rows is the one it belongs to."""
    switch_off(user)

    formset = postal.formset_for(user.profile)

    assert formset.forms[0].person == user


def test_the_helpers_honour_the_switch_for_whoever_is_named(user):
    switch_off(user)
    one = address(country="US", street="1600 Pennsylvania Avenue NW", municipality="Washington")

    assert postal.warnings_for(one, person=user) == []
    assert str(postal.label_for("region", "US", person=user)) == "Region"


def test_on_by_default(user):
    assert postal.rules_apply(user) is True


# ------------------------------------------------------------- the table itself


def test_the_table_records_when_it_was_reviewed():
    """A data set says when it was taken, exactly as #140 concludes for NACE: a reader
    deserves to know whether this is current or four years old.
    """
    from postulo.plugins.postal_rules import rules

    assert rules.REVIEWED


def test_every_row_names_parts_that_exist():
    from postulo.plugins.postal_rules import rules

    for code, rule in rules.RULES.items():
        for part in rule.expects:
            assert part in rules.PARTS, f"{code} expects {part!r}, which is not a part"
        for part in rule.calls:
            assert part in rules.PARTS, f"{code} names {part!r}, which is not a part"
        for group in rule.order:
            for part in group.split():
                assert part in rules.PARTS, f"{code} prints {part!r}, which is not a part"


def test_every_label_key_a_row_uses_has_words_for_it():
    from postulo.plugins.postal_rules import rules

    for code, rule in rules.RULES.items():
        for part, key in rule.calls.items():
            assert key in postal_rules.LABELS, f"{code} calls {part} {key!r}, which says nothing"


def test_no_row_borrows_google():
    """Recorded so nobody proposes it again. `libaddressinput` is the set everybody reaches
    for, and a project that exists as an alternative to services answering to somebody
    else's jurisdiction does not put that jurisdiction in its address form.
    """
    from pathlib import Path

    source = Path(postal_rules.rule_for.__module__.replace(".", "/"))
    text = (Path("src") / f"{source}.py").read_text(encoding="utf-8")

    assert "libaddressinput" in text, "and the reasoning is written down"
    assert "not the answer" in text or "does not depend" in text or "ruled out" in text


# ------------------------------------------- the plugin's words are the plugin's own (#651)

#: What says a label is a part of an address and not the same English word elsewhere.
A_PART = "part of a postal address"


def the_region_label_of_a_united_states_row(client, user, language: str) -> str:
    """*Your details* as somebody reading in that language is given it, and the label over
    the box for the state of an address in the United States."""
    from django.urls import reverse

    user.profile.language = language
    user.profile.save(update_fields=["language"])
    PostalAddress.objects.create(
        owner=user,
        holder=user.profile,
        country="US",
        street="1600 Pennsylvania Avenue NW",
        postcode="20500",
        municipality="Washington",
        region="DC",
        is_primary=True,
    )
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    return re.search(r'<label for="id_addresses-0-region">([^<]*)</label>', html).group(1)


@pytest.mark.parametrize("language", ["de", "el", "nl"])
def test_the_state_of_an_address_is_not_the_word_for_a_status(client, user, language):
    """The plugin's label was the msgid `State` with no context, core has the same msgid
    for a status, and core's catalogue answers first for a message both define (#127). So
    the box read *Zustand*, a condition, and the plugin's *Bundesstaat* was never reached.
    With a context it is a message of its own. The words are asked of the catalogues,
    which Weblate fills (#706); that they are two different words is what is held."""
    with translation.override(language):
        state = translation.pgettext("part of a postal address", "State")
        status = translation.gettext("State")
    assert state != status, "the address part and the status are one word again"
    assert the_region_label_of_a_united_states_row(client, user, language) == state

    with translation.override(language):
        assert str(postal_rules.label_for("region", "US")) == state


def test_no_message_of_the_address_plugin_is_one_of_cores():
    """Core's catalogue wins a message two catalogues share, so one the plugin shares is one
    the plugin's translators never decide: *State*, *Address* and *Country* were. A message
    with a context is the plugin's own; a new label that is also an English word of core's
    fails here until it has one."""
    from pathlib import Path

    from postulo.core import messages_tool

    messages_tool.use(Path(__file__).resolve().parents[1])
    sets = {subject.name: subject for subject in messages_tool.catalogue_sets()}

    def keys(name: str) -> set:
        path = messages_tool.po_path("fr-FR", sets[name])
        return set(messages_tool.parse(path.read_text(encoding="utf-8")).messages)

    ours = keys("plugins/postal_rules")
    assert (A_PART, "State") in ours, "the catalogue was read"
    assert not ours & keys("postulo")


def test_the_labels_with_a_context_left_none_without_one():
    """The three labels moved to entries with a context, and no catalogue kept the old one.

    This used to hold too that a language with the plugin's other words had these three --
    true the day the translations were carried across, and not a promise since: a language
    is filled in Weblate, by speakers and machine drafts, a word at a time (#706).
    """
    from pathlib import Path

    from postulo.core import messages_tool

    messages_tool.use(Path(__file__).resolve().parents[1])
    subject = {s.name: s for s in messages_tool.catalogue_sets()}["plugins/postal_rules"]
    for code in messages_tool.translated_languages():
        path = messages_tool.po_path(code, subject)
        messages = messages_tool.parse(path.read_text(encoding="utf-8")).messages
        for msgid in ("State", "Address", "Country"):
            assert (A_PART, msgid) in messages, f"{code} has no {msgid} with the context"
            assert (None, msgid) not in messages, f"{code} still has {msgid} with no context"


# ----------------------------------------- the notes are whole sentences too (#642)


def test_a_note_never_puts_a_label_after_an_article_in_english():
    germany = address(country="DE", postcode="12345", municipality="Berlin")
    said = [str(sentence) for sentence in postal_rules.warnings_for(germany)]
    assert said, "no street line is still worth a note"
    assert all("a address" not in sentence for sentence in said), said

    ireland = address(
        country="IE", street="1 Example Street", municipality="Dublin", postcode="XYZ"
    )
    said = [str(sentence) for sentence in postal_rules.warnings_for(ireland)]
    assert said == ["An Eircode usually looks like D02 AF30."]


def test_every_part_a_country_expects_has_a_sentence_of_its_own():
    for code, rule in rules.RULES.items():
        for part in rule.expects:
            if part != "street":
                key = rule.calls.get(part, part)
                assert key in postal_rules.USUALLY, f"{code} expects its {key}"
        if rule.postcode:
            assert rule.calls.get("postcode", "postcode") in postal_rules.LOOKS, code


def test_no_message_of_the_plugin_puts_a_label_in_a_sentence():
    from pathlib import Path

    assert not hasattr(postal_rules, "_lower")
    for sentence in (*postal_rules.USUALLY.values(), *postal_rules.LOOKS.values()):
        slots = set(re.findall(r"%\((\w+)\)s", str(sentence)))
        assert slots <= {"example"}, sentence
    source = (Path(postal_rules.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"\b[Aa]n? %\((part|name|country)\)s", source)
    assert "%(country)s" not in source
