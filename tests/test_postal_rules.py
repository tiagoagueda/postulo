"""An address is checked against the rules of its own country — and never refused (#147).

> another internet plugin, multiple postal address, not unique across instance, validades is
> made on a contry basis inside the plugin

Two problems, and only one of them is validation.

**Naming the fields is the harder one, and it is not a translation problem.** A person
reading Postulo in Portuguese who enters a United States address should see *State*; entering
a Portuguese one, *Distrito*. The label depends on the **address's** country and the language
depends on the **reader**, and both vary at once — nothing else in this codebase has that
shape, because every other string is chosen by the reader's language alone.

**And the failure mode to design against is refusing to save.** BFPO addresses, rural routes,
informal settlements, temporary accommodation, and a table that is simply wrong about
somewhere. The rules warn; they never prevent somebody recording where they live.
"""

from __future__ import annotations

import pytest
from django.utils import translation

from postulo.core import postal
from postulo.core.models import PostalAddress
from postulo.plugins import postal_rules

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
    with translation.override("pt-pt"):
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


def test_nothing_the_rules_say_stops_an_address_being_saved(user):
    """The whole of it. An application that will not accept your address is telling you
    something about who it was written for.
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


def test_the_form_saves_an_address_that_fits_nothing(user):
    """The rules reach the form as notes on a row. A row carrying notes is still valid."""
    formset = postal.formset_for(
        user.profile,
        data={
            "addresses-TOTAL_FORMS": "1",
            "addresses-INITIAL_FORMS": "0",
            "addresses-MIN_NUM_FORMS": "0",
            "addresses-MAX_NUM_FORMS": "1000",
            "addresses-0-street": "Rural route 3, past the second gate",
            "addresses-0-postcode": "",
            "addresses-0-municipality": "",
            "addresses-0-country": "PT",
            "addresses-0-kind": "",
            "addresses-0-label": "",
        },
    )

    assert formset.is_valid(), formset.errors
    assert formset.forms[0].country_notes, "and it did say what Portugal usually needs"

    for form in formset.forms:
        form.instance.owner = user
    formset.save()

    assert postal.for_holder(user.profile).count() == 1, "a note is not a refusal"


# ------------------------------------------------------------------ the printing


def test_an_address_prints_the_way_its_country_writes_it():
    lines = postal_rules.render(
        address(country="PT", street="Rua do Exemplo 1", postcode="1000-001", municipality="Lisboa")
    )

    assert lines == ["Rua do Exemplo 1", "1000-001 Lisboa", "Portugal"]


def test_and_differently_where_the_country_does():
    british = postal_rules.render(
        address(
            country="GB", street="10 Downing Street", postcode="SW1A 2AA", municipality="London"
        )
    )
    american = postal_rules.render(
        address(
            country="US",
            street="1600 Pennsylvania Avenue NW",
            postcode="20500",
            municipality="Washington",
            region="DC",
        )
    )

    assert british == ["10 Downing Street", "London", "SW1A 2AA", "United Kingdom"]
    assert american == ["1600 Pennsylvania Avenue NW", "Washington DC 20500", "United States"]


def test_japan_writes_largest_first():
    lines = postal_rules.render(
        address(
            country="JP", street="1-1", postcode="100-8111", municipality="Chiyoda", region="Tokyo"
        )
    )

    assert lines[0] == "100-8111"
    assert lines[-2] == "1-1"


def test_a_country_with_no_rules_prints_in_the_order_it_was_entered():
    lines = postal_rules.render(
        address(country="ZZ", street="Somewhere 1", postcode="0000", municipality="A town")
    )

    assert lines == ["Somewhere 1", "0000", "A town", "ZZ"]


# ---------------------------------------------------------------- switched off


def test_off_is_the_same_path_a_country_with_no_rules_takes(user, monkeypatch):
    """Rather than a second path nobody has seen. Off means neutral labels, no notes, and
    the order it was entered in — which is exactly what an unknown country already gets.
    """
    monkeypatch.setattr(postal, "rules_apply", lambda person: False)
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
