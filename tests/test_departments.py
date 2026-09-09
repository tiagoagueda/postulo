"""A department sits between a company and the people in it.

Deliberately not a company with a parent, though it would fit: a department has no website,
no logo, no identifiers, no industries and no postings of its own. Modelling it as a company
would fill the companies list with things nobody applied to and teach every count to exclude
them. The tests here are mostly about what it is *not*, and about the two directions in which
it must not take anything with it when it goes.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from postulo.jobs.forms import ContactForm
from postulo.jobs.models import Company, Contact, Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture")


@pytest.fixture
def engineering(user, company):
    return Department.objects.create(owner=user, company=company, name="Engineering")


# ------------------------------------------------------------------ the shape


def test_a_contact_can_be_in_a_department(user, company, engineering):
    contact = Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", department=engineering
    )

    assert contact.department == engineering
    assert list(engineering.contacts.all()) == [contact]


def test_both_ends_are_optional_and_that_is_the_normal_case(user, company):
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    empty = Department.objects.create(owner=user, company=company, name="Legal")

    assert contact.department is None
    assert list(empty.contacts.all()) == [], "a team you applied to before knowing anybody"


def test_two_departments_of_one_company_cannot_share_a_name(user, company, engineering):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Department.objects.create(owner=user, company=company, name="Engineering")


def test_the_same_name_at_two_companies_is_not_a_mistake(user, company, engineering):
    other = Company.objects.create(owner=user, name="Black Mesa")
    Department.objects.create(owner=user, company=other, name="Engineering")

    assert Department.objects.for_user(user).filter(name="Engineering").count() == 2


def test_a_contact_cannot_borrow_another_companys_department(user, company, engineering):
    elsewhere = Company.objects.create(owner=user, name="Black Mesa")
    contact = Contact(owner=user, company=elsewhere, name="Gordon", department=engineering)

    with pytest.raises(ValidationError) as refused:
        contact.clean()

    assert "different company" in str(refused.value)


# --------------------------------------------------------- what going away takes


def test_a_department_going_leaves_its_people_where_they_work(user, company, engineering):
    contact = Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", department=engineering
    )

    engineering.delete()

    contact.refresh_from_db()
    assert contact.pk and contact.department is None, "SET_NULL: they still work there"
    assert contact.company == company


def test_a_company_going_takes_its_departments(user, company, engineering):
    company.delete()

    assert not Department.objects.filter(pk=engineering.pk).exists()


# ------------------------------------------------------------------- the form


def test_typing_a_new_name_makes_the_department(client, user, company):
    client.force_login(user)

    client.post(
        reverse("jobs:contact_create"),
        {
            "name": "Cave Johnson",
            "role": "",
            "company": company.pk,
            "email": "",
            "linkedin_url": "",
            "notes": "",
            "new_department": "Engineering",
            "phone_numbers-TOTAL_FORMS": "0",
            "phone_numbers-INITIAL_FORMS": "0",
            "phone_numbers-MIN_NUM_FORMS": "0",
            "phone_numbers-MAX_NUM_FORMS": "1000",
        },
    )

    contact = Contact.objects.for_user(user).get(name="Cave Johnson")
    assert contact.department is not None
    assert contact.department.name == "Engineering"
    assert contact.department.company == company


def test_a_name_the_company_already_has_is_reused(user, company, engineering):
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    form = ContactForm(
        data={
            "name": "Cave Johnson",
            "company": company.pk,
            "role": "",
            "email": "",
            "linkedin_url": "",
            "notes": "",
            "new_department": "engineering",
        },
        instance=contact,
        user=user,
    )
    assert form.is_valid(), form.errors
    form.save()

    contact.refresh_from_db()
    assert Department.objects.for_user(user).count() == 2, "case differs, so it is a new one"
    assert contact.department.name == "engineering"


def test_clearing_the_box_detaches_and_keeps_the_team(user, company, engineering):
    contact = Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", department=engineering
    )
    form = ContactForm(
        data={
            "name": "Cave Johnson",
            "company": company.pk,
            "role": "",
            "email": "",
            "linkedin_url": "",
            "notes": "",
            "new_department": "",
        },
        instance=contact,
        user=user,
    )
    assert form.is_valid(), form.errors
    form.save()

    contact.refresh_from_db()
    assert contact.department is None
    assert Department.objects.filter(pk=engineering.pk).exists(), "a team survives one leaver"


def test_the_suggestions_are_this_companys_teams_only(user, company, engineering, other_user):
    elsewhere = Company.objects.create(owner=user, name="Black Mesa")
    Department.objects.create(owner=user, company=elsewhere, name="Research")
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")

    suggested = ContactForm(instance=contact, user=user).department_suggestions

    assert suggested == ["Engineering"]


# ------------------------------------------------------------------- the page


def test_the_company_page_shows_the_team_beside_the_role(client, user, company, engineering):
    Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", role="Founder", department=engineering
    )
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "Founder" in html and "Engineering" in html


# ---------------------------------------------------------------- the archive


def test_the_team_survives_an_export_and_an_import(user, other_user, company, engineering):
    from postulo.core import export as export_module
    from postulo.core import importer

    Contact.objects.create(owner=user, company=company, name="Cave Johnson", department=engineering)

    archive = zipfile.ZipFile(export_module.write_archive(user))
    document = json.loads(archive.read(export_module.MANIFEST_NAME))
    assert document["companies"][0]["contacts"][0]["department"] == "Engineering"

    importer.load(other_user, archive)

    landed = Contact.objects.for_user(other_user).select_related("department").get()
    assert landed.department is not None
    assert landed.department.name == "Engineering"
    assert landed.department.company.name == "Aperture"


def test_an_archive_from_before_this_still_reads(user, other_user, company):
    """Format 5 named no department, and an archive written yesterday is an archive."""
    from postulo.core import export as export_module
    from postulo.core import importer

    Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    document = json.loads(
        zipfile.ZipFile(export_module.write_archive(user)).read(export_module.MANIFEST_NAME)
    )
    for entry in document["companies"]:
        for contact in entry["contacts"]:
            del contact["department"]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as older:
        older.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))

    assert Contact.objects.for_user(other_user).get().department is None
    assert not Department.objects.for_user(other_user).exists()


# ------------------------------------------------------------------ ownership


def test_one_persons_departments_are_never_anothers(user, other_user, engineering):
    assert not Department.objects.for_user(other_user).exists()
    assert Department.objects.for_user(user).count() == 1
