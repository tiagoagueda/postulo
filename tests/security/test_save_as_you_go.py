"""The endpoint that saves one Settings field as it changes (#656), at the boundary.

Nothing in its address names a record: it is a section and a field, and the record is
always the signed-in person's own profile. So the sweep in `test_isolation_sweep.py` excuses
it, and this is where its isolation is asked for: another account's profile is never read
or written, whatever is posted, and what the address can name is only what a form lists.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from postulo.accounts.models import Profile

pytestmark = pytest.mark.django_db


def turned_on(user) -> Profile:
    profile, _created = Profile.objects.get_or_create(user=user)
    profile.save_as_you_go = True
    profile.save()
    return profile


def test_one_account_saving_never_changes_another(client, user, other_user):
    mine = turned_on(user)
    theirs = turned_on(other_user)
    client.force_login(user)

    response = client.post(
        reverse("settings:save_field", args=["appearance", "density"]),
        {"density": "compact", "user": other_user.pk, "id": theirs.pk, "pk": theirs.pk},
    )

    assert response.status_code == 200
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.density == "compact"
    assert theirs.density == "comfortable"


def test_their_switch_does_not_decide_mine(client, user, other_user):
    Profile.objects.get_or_create(user=user)
    turned_on(other_user)
    client.force_login(user)

    response = client.post(
        reverse("settings:save_field", args=["appearance", "density"]), {"density": "compact"}
    )

    assert response.status_code == 404


def test_a_fields_that_is_not_the_persons_to_change_here_cannot_be_named(client, user):
    turned_on(user)
    client.force_login(user)

    for name in ("user", "id", "plugins_off", "table_settings", "dashboard_widgets"):
        response = client.post(
            reverse("settings:save_field", args=["appearance", name]), {name: "x"}
        )
        assert response.status_code == 404, name


def test_a_post_without_the_token_is_refused(user):
    turned_on(user)
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)

    response = strict.post(
        reverse("settings:save_field", args=["appearance", "density"]), {"density": "compact"}
    )

    assert response.status_code == 403
    assert Profile.objects.get(user=user).density == "comfortable"
