"""The rest of the fields that suggest, what a choice fills, and who may be asked (#1251)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.applications.forms import ApplicationForm, PostingIntakeForm
from postulo.core.models import Tag
from postulo.jobs.forms import CompanyForm, ContactForm
from postulo.jobs.models import Company, Contact
from postulo.resume.forms import (
    CertificationForm,
    CourseForm,
    EducationForm,
    ExperienceForm,
    HonourForm,
    MembershipForm,
)
from postulo.resume.models import Education

pytestmark = pytest.mark.django_db


def ask(client, kind, typed=""):
    return client.get(reverse("core:suggest", args=[kind]), {"q": typed})


def kind_of(form, name):
    html = str(form[name])
    start = html.index('data-suggest="') + len('data-suggest="')
    return html[start : html.index('"', start)].rstrip("/").rsplit("/", 1)[-1]


def test_the_listed_fields_each_name_a_kind(user):
    for form, name, kind in [
        (EducationForm(user=user), "institution", "school"),
        (EducationForm(user=user), "qualification", "qualification"),
        (EducationForm(user=user), "field_of_study", "field-of-study"),
        (EducationForm(user=user), "location", "location"),
        (ExperienceForm(user=user), "role", "job-title"),
        (ExperienceForm(user=user), "location", "location"),
        (CertificationForm(user=user), "issuer", "awarding-body"),
        (CourseForm(user=user), "provider", "place-of-learning"),
        (HonourForm(user=user), "awarded_by", "awarding-body"),
        (MembershipForm(user=user), "organisation", "membership-organisation"),
        (CompanyForm(user=user), "name", "employer"),
        (ContactForm(user=user), "name", "contact-name"),
        (ApplicationForm(user=user), "new_tags", "tag"),
        (PostingIntakeForm(user=user), "company_name", "company"),
    ]:
        assert kind_of(form, name) == kind, (type(form).__name__, name)


def test_the_tag_box_says_what_separates_its_words(user):
    assert 'data-suggest-separator=","' in str(ApplicationForm(user=user)["new_tags"])


def test_the_new_kinds_answer_with_the_persons_own_entries(client, user):
    firm = Company.objects.create(owner=user, name="Seaside College")
    Education.objects.create(
        owner=user,
        institution="Seaside College",
        qualification="Master of Arts",
        field_of_study="Linguistics",
    )
    Contact.objects.create(owner=user, company=firm, name="Ada Lovelace")
    Tag.named(user, ["remote-first"])
    client.force_login(user)

    for kind, typed, wanted in [
        ("school", "sea", "Seaside College"),
        ("qualification", "mas", "Master of Arts"),
        ("field-of-study", "ling", "Linguistics"),
        ("contact-name", "ada", "Ada Lovelace"),
        ("tag", "rem", "remote-first"),
        ("awarding-body", "sea", "Seaside College"),
    ]:
        assert f'data-value="{wanted}"' in ask(client, kind, typed).content.decode(), kind


def test_the_new_kinds_never_offer_another_persons_entries(client, user, other_user):
    firm = Company.objects.create(owner=other_user, name="Secret College")
    Education.objects.create(
        owner=other_user, institution="Secret College", qualification="Secret Degree"
    )
    Contact.objects.create(owner=other_user, company=firm, name="Secret Person")
    Tag.named(other_user, ["secret-tag"])
    client.force_login(user)

    for kind in (
        "school",
        "qualification",
        "field-of-study",
        "contact-name",
        "tag",
        "awarding-body",
        "place-of-learning",
        "membership-organisation",
    ):
        body = ask(client, kind, "").content.decode().lower()
        assert "secret" not in body, kind
