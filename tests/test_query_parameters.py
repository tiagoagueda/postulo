"""A record id taken from the address or a form is whatever the sender typed (#409).

A parameter that only preselects a field must not cost the person the page, and one that
names a record to act on is a record that is not theirs: a 404, never a 500.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.applications.models import Suggestion
from postulo.core.params import as_pk


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", 12), (7, 7), ("abc", None), ("12a", None), ("", None), (None, None)],
)
def test_a_primary_key_is_a_number_or_nothing(value, expected):
    assert as_pk(value) == expected


@pytest.mark.parametrize(
    ("name", "parameter"),
    [
        ("applications:reminder_create", "application"),
        ("jobs:contact_create", "company"),
        ("jobs:posting_create", "company"),
    ],
)
def test_a_preselection_that_is_not_a_number_is_ignored(user, client, name, parameter):
    client.force_login(user)
    answer = client.get(reverse(name), {parameter: "abc"})
    assert answer.status_code == 200
    assert answer.context["form"].initial.get(parameter) is None


def test_accepting_a_suggestion_for_an_application_that_is_not_a_number_is_a_404(user, client):
    client.force_login(user)
    suggestion = Suggestion.objects.create(owner=user, source="mail", summary="A reply")
    answer = client.post(
        reverse("applications:suggestion_action", args=[suggestion.pk, "accept"]),
        {"application": "x"},
    )
    assert answer.status_code == 404
