"""Which languages an instance offers, and what narrowing that must never do.

The interesting behaviour is all in the negative space: nothing stored means everything is
offered, so an instance nobody has narrowed keeps gaining languages as Postulo does; and
narrowing must never rewrite what somebody already chose, because it may be widened again
tomorrow and a hundred silently rewritten profiles cannot be put back.
"""

from __future__ import annotations

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
