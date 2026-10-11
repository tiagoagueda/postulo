"""Five Low findings of the security audit of 2026-10-10 (#771, #773, #774, #775, #787)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError

from postulo.documents.models import CV, CVItem
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------- #774


def test_a_cv_entry_cannot_point_at_somebody_elses_entry_or_cv(user, other_user):
    mine = CV.objects.create(owner=user, name="Main")
    theirs = Experience.objects.create(
        owner=other_user, organisation="Aperture", role="Engineer", start_date=dt.date(2020, 1, 1)
    )
    own = Experience.objects.create(
        owner=user, organisation="Black Mesa", role="Tester", start_date=dt.date(2020, 1, 1)
    )
    kind = CVItem._meta.get_field("content_type").related_model.objects.get_for_model(Experience)

    with pytest.raises(ValidationError):
        CVItem.objects.create(owner=user, cv=mine, content_type=kind, object_id=theirs.pk)
    with pytest.raises(ValidationError):
        CVItem.objects.create(owner=other_user, cv=mine, content_type=kind, object_id=theirs.pk)
    assert CVItem.objects.create(owner=user, cv=mine, content_type=kind, object_id=own.pk).pk
