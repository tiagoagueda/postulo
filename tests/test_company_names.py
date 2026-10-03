"""One employer is one name, whatever case it is typed in, in every script (#546)."""

import pytest
from django.db.models import QuerySet

from postulo.applications.services import get_or_create_company
from postulo.jobs.forms import CompanyForm
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


def test_an_accented_name_in_another_case_is_the_same_company(user):
    first = get_or_create_company(user, "Émile Frères")

    assert get_or_create_company(user, "émile frères") == first
    assert get_or_create_company(user, "ÉMILE FRÈRES") == first
    assert Company.objects.filter(owner=user).count() == 1


def test_a_greek_name_in_capitals_is_the_same_company(user):
    assert get_or_create_company(user, "Οτε") == get_or_create_company(user, "ΟΤΕ")


def test_the_form_refuses_an_accented_name_in_another_case(user):
    Company.objects.create(owner=user, name="Émile Frères")

    form = CompanyForm(data={"name": "ÉMILE FRÈRES"}, user=user)

    assert not form.is_valid()
    assert "name" in form.errors


def test_a_company_made_in_between_is_found_rather_than_a_server_error(user, monkeypatch):
    """Two requests creating one name at once: the second reads the first's row."""
    made = Company.objects.create(owner=user, name="Acme")
    calls = []
    original = QuerySet.first

    def first(self):
        calls.append(1)
        return None if len(calls) == 1 else original(self)

    monkeypatch.setattr(QuerySet, "first", first)

    assert get_or_create_company(user, "Acme") == made
