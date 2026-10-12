"""Oversized and non-ASCII numbers are dropped, not a 500 (#767)."""

from __future__ import annotations

import pytest
from django import forms
from django.urls import reverse

from postulo.core import csv_import
from postulo.jobs.models import Capture, CaptureStatus, Company, Industry

pytestmark = pytest.mark.django_db

THIRTY = "9" * 30


def test_a_bulk_post_with_a_thirty_digit_id_is_a_redirect(client, user):
    Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    for name, action in (("jobs:company_bulk", "tag"), ("listings:bulk", "shortlist")):
        response = client.post(
            reverse(name), {"chosen": [THIRTY, "-4", "0", "٣"], "bulk-action": action}
        )

        assert response.status_code in (200, 302), name


def test_chosen_ids_drops_what_no_row_can_be_called():
    from django.test import RequestFactory

    from postulo.core import bulk

    request = RequestFactory().post("/", {"chosen": [THIRTY, "7", "-1", "abc", "7", str(2**63)]})

    assert bulk.chosen_ids(request) == [7]


def test_discarding_selected_captures_ignores_oversized_and_foreign_digits(client, user):
    capture = Capture.objects.create(
        owner=user, url="https://x.example/1", status=CaptureStatus.PENDING, data={}
    )
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_discard_selected"), {"selected": [THIRTY, "٣", str(capture.pk)]}
    )

    assert response.status_code == 302
    capture.refresh_from_db()
    assert capture.status == CaptureStatus.DISCARDED


def test_a_model_choice_sent_a_huge_number_is_an_invalid_choice():
    class Form(forms.Form):
        one = forms.ModelChoiceField(queryset=Industry.objects.all(), required=False)
        many = forms.ModelMultipleChoiceField(queryset=Industry.objects.all(), required=False)

    form = Form({"one": THIRTY, "many": [THIRTY]})

    assert not form.is_valid()
    assert "one" in form.errors and "many" in form.errors


def test_a_date_with_a_huge_number_in_a_spreadsheet_is_not_a_date():
    assert csv_import.parse_date(f"5 Jan {THIRTY}") is None
    assert csv_import.parse_date(f"{THIRTY}/01/02") is None
    assert csv_import.parse_date("5 Jan 2026").year == 2026
