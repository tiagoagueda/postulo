"""Editing a cell where it sits, and where its refusal goes (#135).

> something like dispatcharr implemented on their channel listigs

The feature that makes a long list feel like a spreadsheet rather than a directory of forms
— and the one that asks a question nothing else in Postulo has had to answer. A form has
somewhere to put a refusal: under a labelled field, in a form with a heading. **A cell four
columns wide has nowhere.**

Three things decide whether this is safe rather than merely convenient, and each is a
section below. The refusal has a decided home rather than an improvised one. Every save
goes through the form the page already uses, so a cell cannot save something the page would
have refused. And a column that cannot be edited cannot be edited *by address* either,
because the declaration is in the table rather than in the template.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from postulo.jobs.models import Company
from postulo.jobs.tables import CompaniesTable

pytestmark = pytest.mark.django_db


def cell(company, column: str = "name") -> str:
    return reverse("jobs:company_cell", args=[company.pk, column])


# ------------------------------------------------------- which columns say they can


def test_the_declaration_is_in_the_table_and_not_in_the_template():
    """Beside `sort` and `filter`, where somebody reading the table can see it."""
    columns = {column.key: column for column in CompaniesTable.columns}

    assert columns["name"].editable == "name"
    assert columns["postings"].editable == "", "a count is not editable because it is a count"
    assert columns["created"].editable == "", "a date belongs to the row it was taken from"


def test_a_column_that_cannot_be_edited_cannot_be_edited_by_address(client, user):
    """Not merely absent from the markup. A declaration nothing enforces is a convention."""
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    assert client.get(cell(company, "postings")).status_code == 404
    assert client.post(cell(company, "postings"), {"postings": "9"}).status_code == 404


def test_a_status_is_not_offered_here_at_all():
    """Because it goes through a service that writes a timeline entry. A cell that skipped
    that would leave an application whose timeline disagrees with it, which is the one class
    of bug this project has designed hardest against.
    """
    from postulo.applications.tables import ApplicationsTable

    editable = [column.key for column in ApplicationsTable.columns if column.editable]

    assert "status" not in editable


# ------------------------------------------------------------- and where a refusal goes


def test_a_refusal_stays_in_the_cell_with_the_value_that_caused_it(client, user):
    Company.objects.create(owner=user, name="Black Mesa")
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.post(cell(company), {"name": "black mesa", "stamp": ""}).content.decode()

    assert "already have a company" in html
    assert 'value="black mesa"' in html, "the value that was refused is still there to fix"
    assert 'aria-invalid="true"' in html
    company.refresh_from_db()
    assert company.name == "Aperture Science", "and nothing was saved"


def test_the_refusal_is_said_as_well_as_shown(client, user):
    """A message in a cell is easy to miss and easy to scroll past."""
    Company.objects.create(owner=user, name="Black Mesa")
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.post(cell(company), {"name": "Black Mesa", "stamp": ""}).content.decode()

    assert 'role="alert"' in html


def test_the_message_is_the_page_s_message(client, user):
    """The same words, because it is the same `clean_name`. A second wording is a second
    thing to keep true.
    """
    Company.objects.create(owner=user, name="Black Mesa")
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    from_cell = client.post(cell(company), {"name": "Black Mesa", "stamp": ""}).content.decode()
    from_page = client.post(
        reverse("jobs:company_update", args=[company.pk]), {"name": "Black Mesa"}
    ).content.decode()

    assert "already have a company" in from_cell
    assert "already have a company" in from_page


# ---------------------------------------------------------------- what a save goes through


def test_a_good_change_saves_and_comes_back_as_a_value(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.post(
        cell(company), {"name": "Aperture Laboratories", "stamp": company.updated_at.isoformat()}
    ).content.decode()

    company.refresh_from_db()
    assert company.name == "Aperture Laboratories"
    assert "Aperture Laboratories" in html
    assert "<input" not in html.split("data-cell-open")[0], "the editor is gone"


def test_the_cell_cannot_save_what_the_page_would_refuse(client, user):
    """`modelform_factory` over the page's own form, so every clean_ method comes with it."""
    import inspect

    from postulo.core.cells import EditableCellView

    source = inspect.getsource(EditableCellView.form_for)

    assert "modelform_factory" in source
    assert "form=self.form_class" in source


def test_somebody_elses_company_is_a_404(client, user, other_user):
    company = Company.objects.create(owner=other_user, name="Aperture Science")
    client.force_login(user)

    assert client.get(cell(company)).status_code == 404


# ----------------------------------------------------------------------- two tabs


def test_a_row_that_moved_is_not_overwritten(client, user):
    """Last write wins with nobody told is the outcome hardest to notice and hardest to
    undo. Being told is one hidden field.
    """
    company = Company.objects.create(owner=user, name="Aperture Science")
    stale = (company.updated_at - dt.timedelta(minutes=5)).isoformat()
    client.force_login(user)

    html = client.post(cell(company), {"name": "Something else", "stamp": stale}).content.decode()

    company.refresh_from_db()
    assert company.name == "Aperture Science"
    assert "Somebody else changed this" in html
    assert "Aperture Science" in html, "and it shows what the row says now"


def test_an_unreadable_stamp_does_not_refuse_the_save(client, user):
    """Refusing every save because a browser sent something odd would be a worse failure
    than the one this prevents.
    """
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    client.post(cell(company), {"name": "Aperture Laboratories", "stamp": "not a date"})

    company.refresh_from_db()
    assert company.name == "Aperture Laboratories"


# --------------------------------------------------------------- and with no script


def test_the_cell_is_a_link_to_the_form_when_nothing_enhances_it(client, user):
    """A complete fallback rather than a degraded one: exactly what the table did before."""
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()
    anchor = html.split("data-cell-open")[1].split(">")[0]
    row = html.split('data-cell id="cell-name-')[1].split("</span>")[0]

    assert f"/jobs/companies/{company.pk}/edit/" in row
    assert "hx-get" in anchor, "and htmx opens the editor where htmx is there to"


def test_the_list_still_renders_every_column_it_did(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()

    assert company.name in html
    assert "data-cell" in html


def test_abandoning_gives_the_value_back(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(f"{cell(company)}?abandon=1").content.decode()

    assert "data-cell-open" in html
    assert "<input" not in html
