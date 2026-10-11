"""Crafted values in a file, a stamp or a subject are refused, not a 500 (#769)."""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError

from postulo.core import cells, kind_files
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


def held_listings(user, **changes):
    company = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=company, title="Tester", posted_at=None)
    document = kind_files.build_document(user, "listings")
    document["listings"][0].update(changes)
    return kind_files.read("listings", json.dumps(document).encode())


@pytest.mark.parametrize("value", [0, 12, True, {"a": 1}, [1], "2026-01-01T25:61", "9999999999"])
def test_a_listing_whose_date_is_not_a_date_is_planned_without_crashing(user, value):
    held = held_listings(user, posted_at=value, closes_at=value)

    plan = kind_files.plan(user, held)

    assert plan["kind"] == "listings"


def test_a_stamp_that_cannot_be_the_exported_at_moment_is_no_moment(user):
    held = held_listings(user)
    held["exported_at"] = "2026-01-01T25:61"

    assert kind_files.plan(user, held)["exported_at"] is None


@pytest.mark.parametrize(
    "stamp", ["2026-01-01T25:61", "2026-13-45", "2026-01-01T10:00:00", "x" * 40]
)
def test_a_stamp_that_is_wrong_is_treated_as_not_moved(stamp, user):
    company = Company.objects.create(owner=user, name="Aperture")

    assert cells.moved_on(company, stamp) is False


def test_a_stamp_in_the_far_future_does_not_hide_a_real_change(user):
    company = Company.objects.create(owner=user, name="Aperture")

    # Not moved is the honest answer to a stamp that says it was drawn after the row changed.
    assert cells.moved_on(company, "2999-01-01T00:00:00+00:00") is False


def test_a_subject_with_a_line_break_is_refused_before_anything_is_spent():
    from postulo.documents.forms import SendDocumentsForm

    form = SendDocumentsForm.__new__(SendDocumentsForm)
    form.cleaned_data = {"subject": "Hello\r\nBcc: someone@evil.example"}
    with pytest.raises(ValidationError) as raised:
        form.clean_subject()
    assert "one line" in str(raised.value)

    form.cleaned_data = {"subject": "A normal subject"}
    assert form.clean_subject() == "A normal subject"
