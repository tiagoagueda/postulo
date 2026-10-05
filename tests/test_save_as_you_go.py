"""Saving a Settings field as it is changed, for the person who asked (#656).

The browser half is `tests/e2e/test_save_as_you_go.py`. What is here is the server's: the
setting, what a field may be, what is refused and in whose words, and what the page draws
only for somebody who has switched it on.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.forms import AccessibilityForm, AppearanceForm
from postulo.accounts.models import Profile
from postulo.core import export
from postulo.core.cells import STALE

pytestmark = pytest.mark.django_db


def profile_of(user, **fields) -> Profile:
    profile, _created = Profile.objects.get_or_create(user=user)
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.save()
    return profile


def field_url(section: str, name: str) -> str:
    return reverse("settings:save_field", args=[section, name])


def stamp_of(profile) -> str:
    profile.refresh_from_db()
    return profile.updated_at.isoformat()


# ------------------------------------------------------------------ the preference


def test_it_is_off_until_somebody_turns_it_on(user):
    assert profile_of(user).save_as_you_go is False


def test_the_switch_is_on_accessibility_and_is_not_itself_saved_as_it_goes():
    assert "save_as_you_go" in AccessibilityForm.base_fields
    assert "save_as_you_go" not in AccessibilityForm.save_as_you_go_fields


def test_every_field_that_saves_as_it_goes_is_a_field_of_its_form():
    for form in (AccessibilityForm, AppearanceForm):
        assert set(form.save_as_you_go_fields) <= set(form.base_fields)


def test_the_navigation_is_not_among_them():
    assert "navigation" not in AppearanceForm.save_as_you_go_fields


def test_the_archive_carries_the_preference_and_its_format_moved():
    assert "save_as_you_go" in export.PROFILE_FIELDS
    assert export.FORMAT_VERSION >= 34


def test_the_candidate_file_does_not_carry_it():
    assert "save_as_you_go" not in export.CANDIDATE_PROFILE_FIELDS


# ------------------------------------------------------------------ the page


def test_the_page_is_the_page_it_was_with_the_setting_off(client, user):
    profile_of(user)
    client.force_login(user)

    html = client.get(reverse("settings:appearance")).content.decode()

    assert "data-save-as-you-go" not in html
    assert "data-save-status" not in html


def test_the_page_says_it_saves_as_you_go_once_it_is_on(client, user):
    profile_of(user, save_as_you_go=True)
    client.force_login(user)

    for name in ("settings:appearance", "settings:accessibility"):
        html = client.get(reverse(name)).content.decode()

        assert "data-save-as-you-go" in html
        assert 'role="status"' in html
        assert "data-save-fields=" in html
    # Save stays, for the keyboard and for scripts off.
    assert ">Save<" in html


def test_a_page_whose_form_cannot_does_not_say_it_does(client, user):
    profile_of(user, save_as_you_go=True)
    client.force_login(user)

    assert "data-save-as-you-go" not in client.get(reverse("settings:locale")).content.decode()


# ------------------------------------------------------------------ the field


def test_a_field_is_saved_on_its_own(client, user):
    profile = profile_of(user, save_as_you_go=True)
    client.force_login(user)

    response = client.post(
        field_url("appearance", "density"),
        {"density": "compact", "stamp": stamp_of(profile)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    assert body["status"].startswith("Saved at ")
    profile.refresh_from_db()
    assert profile.density == "compact"


def test_the_other_fields_and_the_navigation_are_left_alone(client, user):
    profile = profile_of(
        user,
        save_as_you_go=True,
        hidden_nav_items=["listings"],
        nav_order=["listings", "applications"],
        quiet_after_days=30,
    )
    client.force_login(user)

    client.post(field_url("appearance", "density"), {"density": "compact"})

    profile.refresh_from_db()
    assert profile.hidden_nav_items == ["listings"]
    assert profile.nav_order == ["listings", "applications"]
    assert profile.quiet_after_days == 30
    assert profile.theme == "system"


def test_an_unticked_box_is_saved_as_unticked(client, user):
    profile = profile_of(user, save_as_you_go=True, keyboard_shortcuts=True)
    client.force_login(user)

    response = client.post(field_url("accessibility", "keyboard_shortcuts"), {})

    assert response.json()["saved"] is True
    profile.refresh_from_db()
    assert profile.keyboard_shortcuts is False


def test_a_refused_value_is_refused_in_the_pages_words_and_not_stored(client, user):
    profile = profile_of(user, save_as_you_go=True, density="comfortable")
    client.force_login(user)

    response = client.post(field_url("appearance", "density"), {"density": "enormous"})

    assert response.status_code == 422
    body = response.json()
    assert body["saved"] is False
    assert body["status"] == "Not saved"
    assert 'role="alert"' in body["feedback"]
    assert 'id="id_density_error"' in body["feedback"]
    assert "Select a valid choice" in body["feedback"]
    profile.refresh_from_db()
    assert profile.density == "comfortable"


def test_a_number_the_model_refuses_is_refused(client, user):
    profile = profile_of(user, save_as_you_go=True, closing_notice_days=7)
    client.force_login(user)

    response = client.post(
        field_url("appearance", "closing_notice_days"), {"closing_notice_days": "-3"}
    )

    assert response.status_code == 422
    profile.refresh_from_db()
    assert profile.closing_notice_days == 7


def test_a_stale_stamp_is_refused_with_the_cells_sentence(client, user):
    profile = profile_of(user, save_as_you_go=True)
    seen = stamp_of(profile)
    later = timezone.now() + dt.timedelta(minutes=5)
    Profile.objects.filter(pk=profile.pk).update(updated_at=later, density="compact")
    client.force_login(user)

    response = client.post(
        field_url("appearance", "density"), {"density": "comfortable", "stamp": seen}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["saved"] is False
    assert str(STALE) in body["feedback"].replace("&#x27;", "'")
    assert body["value"] == "compact"
    profile.refresh_from_db()
    assert profile.density == "compact"


def test_a_stamp_the_last_save_returned_is_not_stale(client, user):
    profile = profile_of(user, save_as_you_go=True)
    client.force_login(user)

    first = client.post(
        field_url("appearance", "density"), {"density": "compact", "stamp": stamp_of(profile)}
    ).json()
    second = client.post(
        field_url("appearance", "density"), {"density": "comfortable", "stamp": first["stamp"]}
    )

    assert second.status_code == 200


# ------------------------------------------------------------------ who may


def test_it_is_a_404_for_a_person_who_has_not_asked(client, user):
    profile = profile_of(user)
    client.force_login(user)

    response = client.post(field_url("appearance", "density"), {"density": "compact"})

    assert response.status_code == 404
    profile.refresh_from_db()
    assert profile.density == "comfortable"


@pytest.mark.parametrize(
    ("section", "name"),
    [
        ("appearance", "navigation"),
        ("appearance", "hidden_nav_items"),
        ("appearance", "plugins_off"),
        ("accessibility", "save_as_you_go"),
        ("accessibility", "density"),
        ("language", "language"),
        ("capture", "keep_page_source"),
        ("nowhere", "density"),
    ],
)
def test_a_field_that_is_not_on_the_forms_list_cannot_be_named(client, user, section, name):
    profile_of(user, save_as_you_go=True)
    client.force_login(user)

    response = client.post(field_url(section, name), {name: "on"})

    assert response.status_code == 404


def test_a_get_is_not_answered(client, user):
    profile_of(user, save_as_you_go=True)
    client.force_login(user)

    assert client.get(field_url("appearance", "density")).status_code == 405


def test_a_stranger_is_sent_to_sign_in(client):
    response = client.post(field_url("appearance", "density"), {"density": "compact"})

    assert response.status_code == 302
    assert "login" in response["Location"]
