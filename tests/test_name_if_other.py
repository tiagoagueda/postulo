"""The "Name, if Other" box is drawn when the kind is Other, and blanked otherwise (#284).

The display side always followed the rule -- `scheme_label` reads the label only for Other
-- and the form contradicted it with an empty box on every row. Two halves: the row marks
itself so the stylesheet can hide the box unless Other is chosen or a name is already
there, and the forms blank a name whose kind is not Other, which used to be stored
invisibly.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------- the forms


def test_a_company_identifier_keeps_its_name_only_for_other(user):
    from postulo.jobs.forms import CompanyIdentifierForm

    form = CompanyIdentifierForm({"scheme": "wikidata", "value": "Q95", "label": "typed"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["label"] == "", "a name with a kind that has one is blanked"

    form = CompanyIdentifierForm({"scheme": "other", "value": "X-1", "label": ""})
    assert not form.is_valid() and "label" in form.errors

    form = CompanyIdentifierForm({"scheme": "other", "value": "X-1", "label": "Payroll id"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["label"] == "Payroll id"


def test_a_person_identifier_follows_the_same_rule(user):
    from postulo.accounts.forms import PersonIdentifierForm

    form = PersonIdentifierForm({"scheme": "orcid", "value": "0000-0002-1825-0097", "label": "x"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["label"] == ""

    form = PersonIdentifierForm({"scheme": "other", "value": "42", "label": ""})
    assert not form.is_valid() and "label" in form.errors


def test_a_phone_and_an_address_blank_the_name_with_the_kind(user):
    from postulo.core.phone_numbers import PhoneNumberForm
    from postulo.core.postal import PostalAddressForm

    phone = PhoneNumberForm({"kind": "mobile", "label": "typed", "number": "+351912345678"})
    assert phone.is_valid(), phone.errors
    assert phone.cleaned_data["label"] == ""

    address = PostalAddressForm({"kind": "home", "label": "typed", "street": "Rua A"})
    assert address.is_valid(), address.errors
    assert address.cleaned_data["label"] == ""


# ---------------------------------------------------------------- the rows


def rows_of(html: str, marker: str) -> list[str]:
    """The opening tag of each marked row: what it carries is what the stylesheet reads."""
    return re.findall(rf'<div class="{marker} grid[^>]*>', html)


def test_the_identifier_row_is_one_component_marked_for_the_stylesheet(client, user):
    """Both pages draw the same row, and a row without a stored name carries no
    `data-has-name`, so the stylesheet hides the box unless Other is chosen."""
    client.force_login(user)
    for url in (reverse("jobs:company_create"), reverse("accounts:profile")):
        html = client.get(url).content.decode()
        rows = rows_of(html, "name-if-other-row")
        assert rows, url
        assert "data-name-if-other" in html, url
        assert not any("data-has-name" in row for row in rows), "nothing stored yet"
        assert "Name, if Other" not in html, "the box says Name, since it shows only for Other"


def test_a_row_that_already_holds_a_name_keeps_its_box(client, user):
    """Hiding a stored value would make it invisible and unremovable, whatever the kind."""
    from postulo.jobs.models import Company, CompanyIdentifier

    company = Company.objects.create(owner=user, name="Aperture")
    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="wikidata", value="Q95", label="stray"
    )
    client.force_login(user)

    html = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()
    assert re.search(r"name-if-other-row grid[^>]*data-has-name", html), "the box stays"


def test_the_short_rows_are_marked_too(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "name-if-other-row-short" in html and "data-name-if-other" in html


def test_the_stylesheet_follows_the_select():
    """`:has()` on the row, so choosing Other reveals the box with no script."""
    from pathlib import Path

    css = Path(__file__).resolve().parents[1] / "src/postulo/static/css/app.css"
    text = css.read_text(encoding="utf-8")
    assert 'option[value="other"]:checked' in text and "[data-has-name]" in text
