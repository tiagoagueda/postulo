"""A dated entry that ends before it starts is refused, in every kind of entry (#621).

Only Experience checked the order, and it did so with a string no catalogue had, so every
language showed it in English. The check lives on `ResumeItemForm` now, keyed by each
form's `date_range`, and the messages are lazy translations.
"""

from __future__ import annotations

import pytest
from django.utils.functional import Promise

from postulo.resume.forms import (
    CertificationForm,
    EducationForm,
    ExperienceForm,
    ProjectForm,
    ResumeItemForm,
)

pytestmark = pytest.mark.django_db

EARLIER, LATER = "2021-01-01", "2023-01-01"

CASES = [
    (
        ExperienceForm,
        {"role": "Tester", "organisation": "Aperture"},
        "start_date",
        "end_date",
    ),
    (
        EducationForm,
        {"qualification": "BSc", "institution": "Aperture U"},
        "start_date",
        "end_date",
    ),
    (ProjectForm, {"name": "Postulo"}, "start_date", "end_date"),
    (
        CertificationForm,
        {"name": "Cert", "issuer": "Aperture"},
        "issued_on",
        "expires_on",
    ),
]
IDS = ["experience", "education", "project", "certification"]


@pytest.mark.parametrize(("form_class", "base", "start", "end"), CASES, ids=IDS)
def test_end_before_start_is_refused(user, form_class, base, start, end):
    form = form_class({**base, start: LATER, end: EARLIER}, user=user)
    assert not form.is_valid()
    assert list(form.errors) == [end]


@pytest.mark.parametrize(("form_class", "base", "start", "end"), CASES, ids=IDS)
def test_same_day_and_open_end_are_accepted(user, form_class, base, start, end):
    assert form_class({**base, start: EARLIER, end: EARLIER}, user=user).is_valid()
    assert form_class({**base, start: EARLIER}, user=user).is_valid()


def test_experience_message_is_english_by_default(user):
    form = ExperienceForm(
        {"role": "T", "organisation": "A", "start_date": LATER, "end_date": EARLIER}, user=user
    )
    form.is_valid()
    assert form.errors["end_date"] == ["This is before the start date."]


def test_messages_are_lazy_translations_so_every_language_applies():
    assert isinstance(ResumeItemForm.end_before_start_message, Promise)
    assert isinstance(CertificationForm.end_before_start_message, Promise)
    assert str(CertificationForm.end_before_start_message) == (
        "This is before the date it was issued."
    )
