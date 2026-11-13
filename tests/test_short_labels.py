"""One- or two-word labels for sectors and industries, the full name kept beside (#1248)."""

from __future__ import annotations

import pytest
from django.template import Context, engines
from django.urls import reverse
from django.utils import translation

from postulo.core import search
from postulo.jobs import industries
from postulo.jobs.models import Company, Industry

LONGEST_WORDS, LONGEST_CHARACTERS = 2, 20


def badge(industry) -> str:
    source = '{% cotton industry-badge :industry="industry" / %}'
    return engines["django"].engine.from_string(source).render(Context({"industry": industry}))


def _english(label) -> str:
    return industries._english(label)


def test_every_section_and_division_has_a_short_label_and_nothing_else_does():
    """The labels cover exactly the 22 sections and 87 divisions of the vendored data."""
    assert set(industries.SECTION_SHORT) == set(industries.classification()["sections"])
    assert set(industries.DIVISION_SHORT) == set(industries.classification()["divisions"])
    assert (len(industries.SECTION_SHORT), len(industries.DIVISION_SHORT)) == (22, 87)


@pytest.mark.parametrize("table", ["SECTION_SHORT", "DIVISION_SHORT"])
def test_a_short_label_is_one_or_two_words_and_at_most_twenty_characters(table):
    """The rule that keeps a label from creeping back into a phrase."""
    for key, label in getattr(industries, table).items():
        text = _english(label)
        assert text == text.strip() and text, key
        assert len(text.split()) <= LONGEST_WORDS, (key, text)
        assert len(text) <= LONGEST_CHARACTERS, (key, text)


@pytest.mark.parametrize("table", ["SECTION_SHORT", "DIVISION_SHORT"])
def test_no_two_labels_in_one_list_are_equal(table):
    """A clash is settled with the two-word form, so a list can always tell its rows apart."""
    texts = [_english(label).casefold() for label in getattr(industries, table).values()]
    assert len(texts) == len(set(texts))


def test_a_short_label_is_shorter_than_the_name_it_stands_for():
    """The information section is a word, not the Eurostat sentence."""
    assert industries.short_section_name("K") == "Technology"
    assert "Telecommunication, computer programming" in industries.section_name("K", "en")
    assert industries.short_division_name("62") == "Software"
    assert industries.short_division_name("") == ""
    assert industries.short_section_name("Z") == ""


def test_a_label_without_a_translation_reads_in_english_and_one_with_is_translated():
    """gettext gives the English label where a language has none (no machine guess)."""
    with translation.override("fi"):
        assert industries.short_division_name("62")


@pytest.mark.django_db
def test_a_seeded_name_is_drawn_short_and_the_full_name_is_the_tooltip(user):
    """The badge shows the label; the full name is its title and accessible description."""
    industry = Industry.named(user, ["Computer programming, consultancy and related activities"])[0]
    assert industry.code == "62" and industry.short_name == "Software"

    html = badge(industry)

    assert "<bdi>Software</bdi>" in html
    assert 'aria-description="Computer programming, consultancy and related activities"' in html
    assert "Computer programming, consultancy and related activities · " in html
    assert "NACE 62" in html


@pytest.mark.django_db
def test_a_word_of_ones_own_is_drawn_as_typed(user):
    """Only the seeded NACE names have short forms; the person's words never change."""
    (industry,) = Industry.named(user, ["Quantum gardening"])
    assert industry.code == "" and industry.short_name == "Quantum gardening"
    html = badge(industry)
    assert "<bdi>Quantum gardening</bdi>" in html and "aria-description" not in html


@pytest.mark.django_db
def test_a_seeded_name_the_person_renamed_keeps_their_name(user):
    """Renaming takes the code away, so the new name is drawn as it is."""
    (industry,) = Industry.named(user, ["Telecommunication"])
    assert industry.short_name == "Telecoms"
    industry.name = "Phone lines"
    industry.save()
    assert industry.code == "" and industry.short_name == "Phone lines"


@pytest.mark.django_db
def test_the_company_list_shows_the_short_labels_with_the_full_name_beside(client, user):
    """The sector and industry cells draw the label; the full name is the tooltip."""
    company = Company.objects.create(owner=user, name="Aperture")
    company.industries.set(
        Industry.named(user, ["Computer programming, consultancy and related activities"])
    )
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()

    assert ">Technology</span>" in html and "<bdi>Software</bdi>" in html
    assert industries.section_name("K", "en") in html, "the full sector name is the tooltip"
    assert 'aria-description="Computer programming' in html


@pytest.mark.django_db
def test_the_companies_are_found_by_the_short_label_the_full_name_or_their_own_word(client, user):
    """Typing `software` or `programming` finds the same company, in the table and the search."""
    company = Company.objects.create(owner=user, name="Aperture")
    company.industries.set(
        Industry.named(user, ["Computer programming, consultancy and related activities"])
    )
    other = Company.objects.create(owner=user, name="Black Mesa")
    other.industries.set(Industry.named(user, ["Quantum gardening"]))
    client.force_login(user)
    url = reverse("jobs:company_list")

    for word in ("software", "programming", "Computer programming, consultancy"):
        html = client.get(url, {"industry": word}).content.decode()
        assert "Aperture" in html and "Black Mesa" not in html, word
        queried = Company.objects.with_notes(user).filter(search.company_match(word))
        assert list(queried) == [company], word
    gardening = client.get(url, {"industry": "gardening"}).content.decode()
    assert "Black Mesa" in gardening and "Aperture" not in gardening
    assert list(Company.objects.with_notes(user).filter(search.company_match("gardening"))) == [
        other
    ]


@pytest.mark.django_db
def test_the_sector_filter_offers_the_short_labels():
    """The filter's choices are the 22 letters with their short labels."""
    from postulo.jobs.tables import SectorChoices

    choices = dict(SectorChoices())
    assert choices["K"] == "Technology" and len(choices) == 22
