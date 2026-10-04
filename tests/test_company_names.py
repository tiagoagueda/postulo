"""One employer is one name, whatever case it is typed in, in every script (#546)."""

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import QuerySet

from postulo.applications.services import get_or_create_company
from postulo.jobs.forms import CompanyForm
from postulo.jobs.models import Company, Department

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


def test_the_database_itself_refuses_a_name_in_another_case(user):
    """Not only the Python-side lookups: the constraint reads the folded key."""
    Company.objects.create(owner=user, name="Émile  Frères")

    with pytest.raises(IntegrityError), transaction.atomic():
        Company.objects.create(owner=user, name="émile frères")


def test_a_renamed_company_gets_its_key_again(user):
    company = Company.objects.create(owner=user, name="Acme")

    company.name = "ΟΤΕ"
    company.save(update_fields=["name"])

    company.refresh_from_db()
    assert company.name_key == "οτε"


def test_two_departments_of_one_company_differ_by_more_than_capitals(user):
    company = Company.objects.create(owner=user, name="Aperture")
    Department.objects.create(owner=user, company=company, name="Ingénierie")

    with pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.create(owner=user, company=company, name="INGÉNIERIE")
    other = Company.objects.create(owner=user, name="Black Mesa")
    Department.objects.create(owner=user, company=other, name="INGÉNIERIE")


def test_the_clash_listing_is_empty_when_nothing_clashes(user, capsys):
    Company.objects.create(owner=user, name="Acme")

    call_command("company_name_clashes")

    assert "No clashes" in capsys.readouterr().out


BEFORE = [("jobs", "0024_url_key")]
AFTER = [("jobs", "0025_name_keys")]


def _old_rows(clash: bool):
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    owner = old.get_model("accounts", "User").objects.create(username="folder")
    company = old.get_model("jobs", "Company")
    first = company.objects.create(owner=owner, name="Émile Frères")
    second = company.objects.create(owner=owner, name="émile frères" if clash else "Acme")
    old.get_model("jobs", "Department").objects.create(owner=owner, company=first, name="Legal")
    return first, second


def _leave_at_latest():
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_the_migration_refuses_when_two_companies_clash_and_lists_them():
    first, second = _old_rows(clash=True)
    try:
        with pytest.raises(RuntimeError) as refused:
            MigrationExecutor(connection).migrate(AFTER)
        message = str(refused.value)
        assert f"#{first.pk}" in message and f"#{second.pk}" in message
        assert "Émile Frères" in message and "émile frères" in message
        assert f"owner {first.owner_id}" in message
    finally:
        # The refused step rolled back where the database can; clear the clash and finish.
        executor = MigrationExecutor(connection)
        executor.loader.project_state(BEFORE).apps.get_model("jobs", "Company").objects.filter(
            pk=second.pk
        ).delete()
        _leave_at_latest()


@pytest.mark.django_db(transaction=True)
def test_the_migration_fills_the_keys_when_nothing_clashes():
    _old_rows(clash=False)

    executor = MigrationExecutor(connection)
    executor.migrate(AFTER)
    new = executor.loader.project_state(AFTER).apps

    assert sorted(new.get_model("jobs", "Company").objects.values_list("name_key", flat=True)) == [
        "acme",
        "émile frères",
    ]
    assert list(new.get_model("jobs", "Department").objects.values_list("name_key", flat=True)) == [
        "legal"
    ]
    _leave_at_latest()


@pytest.mark.django_db(transaction=True)
def test_the_clash_listing_reads_rows_that_predate_the_key():
    from postulo.jobs import name_clashes

    _old_rows(clash=True)
    old = MigrationExecutor(connection).loader.project_state(BEFORE).apps
    company, department = old.get_model("jobs", "Company"), old.get_model("jobs", "Department")
    try:
        lines = name_clashes.find(company, department)
        assert len(lines) == 1 and "companies" in lines[0]
    finally:
        company.objects.filter(name="émile frères").delete()
        _leave_at_latest()
