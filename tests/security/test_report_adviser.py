"""A person's report names only contacts that person can see (SEC3-021, #1276).

A group-owned employment-service company is visible to every member, but a contact added
there stays its author's. The report's adviser must be one the reader may see.
"""

import datetime as dt

import pytest
from django.contrib.auth.models import Group

from postulo.applications import reports
from postulo.jobs.models import Company, CompanyKind, Contact

pytestmark = pytest.mark.django_db


def test_the_adviser_is_never_another_member_s_contact(user, other_user):
    group = Group.objects.create(name="Household")
    group.user_set.add(user, other_user)
    office = Company.objects.create(
        owner_group=group, name="Job Centre", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    Contact.objects.create(owner=other_user, company=office, name="Their Adviser")
    period = reports.Period(dt.date(2026, 1, 1), dt.date(2026, 1, 31))

    mine = reports.build(user, period)
    assert mine.office == "Job Centre"
    assert mine.adviser == ""

    Contact.objects.create(owner=user, company=office, name="My Adviser")
    assert reports.build(user, period).adviser == "My Adviser"
