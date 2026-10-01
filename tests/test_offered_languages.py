"""Which languages an instance offers, and what narrowing that must never do.

The interesting behaviour is all in the negative space: nothing stored means everything is
offered, so an instance nobody has narrowed keeps gaining languages as Postulo does; and
narrowing must never rewrite what somebody already chose, because it may be widened again
tomorrow and a hundred silently rewritten profiles cannot be put back.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.accounts.forms import language_choices
from postulo.core import site
from postulo.core.models import SiteSettings
from postulo.core.server_forms import OfferedLanguagesForm

pytestmark = pytest.mark.django_db


@pytest.fixture
def settings_row(db):
    """The policy row, actually saved.

    `site.current()` answers with an unsaved `SiteSettings()` when nobody has stored one,
    which is right for reading and useless for a test that wants to change it: `pk` is None
    and every update matches nothing. The row lives at pk=1.
    """
    row, _made = SiteSettings.objects.update_or_create(
        pk=1, defaults={"default_language": "", "offered_languages": []}
    )
    return row


def offered_codes() -> set[str]:
    """Every code the picker actually shows, across its groups."""
    return {code for _group, entries in language_choices()[1:] for code, _label in entries}


# ------------------------------------------------------------- nothing stored


def test_nothing_stored_offers_everything(settings_row):
    assert site.offered_languages() == []
    assert site.offers("de") and site.offers("pt-pt")
    assert len(offered_codes()) > 30


def test_a_language_added_later_is_offered_by_itself(settings_row):
    """Which is why an empty list is stored rather than a list naming them all."""
    assert site.offers("a-language-postulo-does-not-have-yet") is True


# ----------------------------------------------------------------- narrowing


def test_narrowing_removes_the_rest_from_the_picker(settings_row):
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de", "pt-pt"])

    assert offered_codes() == {"de", "pt-pt"}
    assert not site.offers("fr-fr")


def test_a_stored_code_postulo_no_longer_speaks_is_passed_over(settings_row):
    SiteSettings.objects.filter(pk=settings_row.pk).update(
        offered_languages=["de", "not-a-language"]
    )

    assert site.offered_languages() == ["de"]


def test_an_administrator_is_not_exempt(client, django_user_model, settings_row):
    """What the instance offers is what it offers; two rules where one will do drift apart."""
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de"])
    admin = django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )
    client.force_login(admin)

    assert offered_codes() == {"de"}


# ------------------------------------------------------ what it never rewrites


def test_a_withdrawn_language_leaves_the_persons_choice_alone(user, settings_row):
    user.profile.language = "fr-fr"
    user.profile.save(update_fields=["language"])
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de"])

    user.profile.refresh_from_db()
    assert user.profile.language == "fr-fr", "stored, so offering it again restores it"


def test_a_withdrawn_language_is_not_applied_to_the_page(client, user, settings_row):
    from postulo.core.middleware import UserPreferencesMiddleware

    user.profile.language = "fr-fr"
    user.profile.save(update_fields=["language"])
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de"])

    assert not site.offers("fr-fr")
    assert UserPreferencesMiddleware._offered("fr-fr") is False
    assert UserPreferencesMiddleware._offered("de") is True


def test_offering_it_again_brings_the_person_back(user, settings_row):
    user.profile.language = "fr-fr"
    user.profile.save(update_fields=["language"])
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de"])
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=[])

    user.profile.refresh_from_db()
    assert user.profile.language == "fr-fr"
    assert site.offers("fr-fr")


# --------------------------------------------------------------- what it refuses


def test_offering_nothing_is_refused(settings_row):
    form = OfferedLanguagesForm(data={"offered_languages": []}, instance=settings_row)

    assert not form.is_valid()
    assert "At least one language" in str(form.errors)


def test_the_default_language_cannot_stop_being_offered(settings_row):
    SiteSettings.objects.filter(pk=settings_row.pk).update(default_language="pt-pt")
    form = OfferedLanguagesForm(data={"offered_languages": ["de"]}, instance=site.current())

    assert not form.is_valid()
    assert "português" in str(form.errors), "the message names it rather than saying no"


def test_ticking_everything_is_stored_as_nothing(settings_row):
    every = [code for code, _name in OfferedLanguagesForm(instance=settings_row).every]
    form = OfferedLanguagesForm(data={"offered_languages": every}, instance=settings_row)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["offered_languages"] == [], "so a later language is offered too"


def test_a_narrowed_list_is_stored_as_itself(settings_row):
    form = OfferedLanguagesForm(data={"offered_languages": ["de", "pt-pt"]}, instance=settings_row)

    assert form.is_valid(), form.errors
    assert sorted(form.cleaned_data["offered_languages"]) == ["de", "pt-pt"]


# ------------------------------------------------------------------- the page


def test_the_page_offers_the_list_and_saves_it(client, django_user_model, settings_row):
    admin = django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )
    client.force_login(admin)

    html = client.get(reverse("server:defaults")).content.decode()
    assert "Languages this instance offers" in html
    assert 'name="offered_languages"' in html

    client.post(
        reverse("server:defaults"),
        {"offered_languages": ["de", "pt-pt"], "offered_languages_submit": "1"},
    )

    assert sorted(site.offered_languages()) == ["de", "pt-pt"]


def test_saving_the_other_form_does_not_disturb_the_list(client, django_user_model, settings_row):
    """Two forms on one page: changing the instance name must not clear the languages."""
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de"])
    admin = django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )
    client.force_login(admin)

    client.post(
        reverse("server:defaults"),
        {
            "instance_name": "Somewhere",
            "tagline": "",
            "default_language": "",
            "default_time_zone": "",
        },
    )

    assert site.offered_languages() == ["de"]
    assert site.instance_name() == "Somewhere"


# ---------------------------------------------- nothing stored, on the page (#322)


def boxes(html: str) -> tuple[set[str], set[str]]:
    """The language boxes on the page: every code, and the codes that are ticked."""
    inputs = re.findall(r'<input[^>]*name="offered_languages"[^>]*>', html)
    codes = {re.search(r'value="([^"]+)"', box).group(1) for box in inputs}
    ticked = {re.search(r'value="([^"]+)"', box).group(1) for box in inputs if " checked" in box}
    return codes, ticked


@pytest.fixture
def administrator(client, django_user_model):
    admin = django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )
    client.force_login(admin)
    return admin


def test_a_fresh_instance_shows_every_language_ticked(client, administrator):
    """The page says all of them are offered, so every box says it too.

    No row is stored at all here. The form's initial value came from the record, an empty
    list, and that overrode the field's own "all of them": thirty-nine empty boxes under a
    sentence saying every language is offered (#322).
    """
    assert not SiteSettings.objects.exists()

    html = client.get(reverse("server:defaults")).content.decode()
    codes, ticked = boxes(html)

    assert "All of them are offered" in html
    assert len(codes) > 30
    assert ticked == codes


def test_a_saved_row_with_nothing_narrowed_shows_every_language_ticked(
    client, administrator, settings_row
):
    """The same once *Defaults* has been saved: an empty list is the setting, not its absence."""
    codes, ticked = boxes(client.get(reverse("server:defaults")).content.decode())

    assert codes and ticked == codes


def test_saving_the_page_as_shown_keeps_offering_everything(client, administrator, settings_row):
    """Unchanged in, unchanged out: still nothing stored, so a later language is offered too.

    A list naming today's languages would look the same on this page and freeze the set on
    the day somebody pressed Save.
    """
    before = offered_codes()
    _codes, ticked = boxes(client.get(reverse("server:defaults")).content.decode())

    response = client.post(
        reverse("server:defaults"),
        {"offered_languages": sorted(ticked), "offered_languages_submit": "1"},
    )

    assert response.status_code == 302
    assert SiteSettings.get().offered_languages == []
    assert site.offered_languages() == []
    assert site.offers("a-language-postulo-does-not-have-yet")
    assert offered_codes() == before


def test_a_narrowed_list_ticks_its_own_and_no_others(client, administrator, settings_row):
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["de", "pt-pt"])

    html = client.get(reverse("server:defaults")).content.decode()
    _codes, ticked = boxes(html)

    assert ticked == {"de", "pt-pt"}
    assert "All of them are offered" not in html


def test_only_codes_postulo_no_longer_speaks_is_everything(client, administrator, settings_row):
    """`site.offered_languages()` passes such a code over and so offers everything; the
    boxes follow what is in force, not what happens to be written in the row."""
    SiteSettings.objects.filter(pk=settings_row.pk).update(offered_languages=["not-a-language"])

    html = client.get(reverse("server:defaults")).content.decode()
    codes, ticked = boxes(html)

    assert site.offered_languages() == []
    assert "All of them are offered" in html
    assert ticked == codes


def test_a_refused_list_comes_back_as_it_was_sent(client, administrator, settings_row):
    """What was posted is what is shown beside the error, not every box ticked over it."""
    SiteSettings.objects.filter(pk=settings_row.pk).update(default_language="pt-pt")

    response = client.post(
        reverse("server:defaults"),
        {"offered_languages": ["de"], "offered_languages_submit": "1"},
    )
    _codes, ticked = boxes(response.content.decode())

    assert response.status_code == 200
    assert ticked == {"de"}
    assert site.offered_languages() == []


# ------------------------------------ a region in brackets is part of the name (#322)


def test_a_partly_translated_regional_language_keeps_its_region(client, administrator, monkeypatch):
    """The row's name was cut at its first " (" to take off a percentage this list never
    appended, and took the region with it: "français" where the page means "français
    (France)", and nothing to tell it from "français (Canada)" the day that is added."""
    from postulo.core import languages

    monkeypatch.setattr(
        languages,
        "translation_status",
        lambda: {"fr-fr": {"total": 100, "translated": 40, "drafts": 40, "percent": 40}},
    )

    html = client.get(reverse("server:defaults")).content.decode()
    row = next(
        row
        for row in re.findall(r"<label[^>]*>(.*?)</label>", html, re.S)
        if 'value="fr-fr"' in row
    )

    assert '<span lang="fr-fr">français (France)</span>' in row
