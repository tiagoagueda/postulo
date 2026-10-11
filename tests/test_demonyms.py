"""Nationalities named as nationalities -- "Portuguese", not "Portugal" (#865)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.core import demonyms, phones
from postulo.documents import rendering
from postulo.documents.models import CV

pytestmark = pytest.mark.django_db


def test_every_country_the_list_offers_has_a_row_and_no_row_is_for_a_country_it_lacks():
    assert [row[0] for row in demonyms.ROWS] == sorted(phones.BY_CODE)


def test_a_row_is_well_formed_and_says_where_it_stands_on_someone_elses_word():
    for code, english, french_m, french_f, pt_m, pt_f, hand in demonyms.ROWS:
        words = (english, french_m, french_f, pt_m, pt_f)
        assert all(word == word.strip() for word in words), code
        assert not hand or set(hand.split(",")) <= set(demonyms.LANGUAGES), code
        # A language is all there or not there: both genders, or none.
        assert bool(french_m) == bool(french_f) and bool(pt_m) == bool(pt_f), code
        if english:
            assert english[0].isupper(), code


@pytest.mark.parametrize(
    ("code", "language", "feminine", "word"),
    [
        ("PT", "en", False, "Portuguese"),
        ("PT", "en-GB", True, "Portuguese"),
        ("PT", "fr", False, "portugais"),
        ("PT", "fr-FR", True, "portugaise"),
        ("PT", "pt-PT", False, "português"),
        ("PT", "pt-BR", True, "portuguesa"),
        ("BE", "fr", True, "belge"),
        ("DE", "pt", True, "alemã"),
        ("pt", "en", False, "Portuguese"),
        ("PT", "de", False, ""),
        ("XX", "en", False, ""),
        ("SJ", "en", False, ""),
    ],
)
def test_the_word_is_the_languages_and_the_genders(code, language, feminine, word):
    assert demonyms.demonym(code, language, feminine=feminine) == word


@pytest.mark.parametrize(
    ("gender", "feminine"),
    [
        ("Female", True),
        ("Féminin", True),
        ("Feminino", True),
        ("female", True),
        ("Male", False),
        ("Non-binary", False),
        ("agender", False),
        ("", False),
        (None, False),
    ],
)
def test_only_the_first_answer_of_the_list_asks_for_the_feminine(gender, feminine):
    assert demonyms.is_feminine(gender) is feminine


def test_a_language_with_no_table_and_a_country_with_no_word_fall_back_to_the_country():
    assert demonyms.nationality("PT", "de") == "Portugal"
    assert demonyms.nationality("SJ", "en") == "Svalbard and Jan Mayen"
    assert demonyms.nationalities(["PT", "XX", "BR"], "fr", "Féminin") == [
        "portugaise",
        "brésilienne",
    ]


def test_the_menu_label_keeps_the_country_for_searching():
    assert demonyms.menu_label("PT", "en") == "Portuguese (Portugal)"
    assert demonyms.menu_label("PT", "fr", "Féminin") == "portugaise (Portugal)"
    assert demonyms.menu_label("PT", "de") == "Portugal"
    labels = [label for _code, label in demonyms.choices("en")]
    assert labels == sorted(labels, key=str.casefold) and len(labels) == len(phones.COUNTRIES)


def holding(user, gender="", **profile):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    for name, value in {"nationalities": ["PT", "BR"], "gender": gender, **profile}.items():
        setattr(user.profile, name, value)
    user.profile.save()
    return user


def test_the_menus_on_your_details_name_the_people_and_keep_the_country(client, user):
    holding(user, gender="Féminin", record_language="fr-FR")
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert re.search(r'<option value="PT"[^>]*selected[^>]*>\s*portugaise \(Portugal\)', html)
    assert "Portuguese (Portugal)" not in html, "the record's language, not another's"


def test_a_cv_prints_the_word_in_its_language_agreeing_with_the_gender(user):
    holding(user, gender="Feminino")
    cv = CV.objects.create(owner=user, name="Main", language="fr", show_nationality=True)
    assert "Nationality" not in rendering.cv_text(cv), "French words, not English ones"
    assert "portugaise, brésilienne" in rendering.cv_text(cv)

    user.profile.gender = ""
    user.profile.save()
    assert "portugais, brésilien" in rendering.cv_text(cv)

    user.profile.gender = "Female"
    user.profile.save()
    cv.language = "de"
    cv.save()
    assert "Portugal, Brazil" in rendering.cv_text(cv), "German has no table: the countries"
