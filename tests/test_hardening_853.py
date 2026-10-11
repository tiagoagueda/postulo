"""The logo is changed from a dialog on the edit page, with a form of its own (#853)."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.jobs.models import Company
from tests.test_logos import an_image, web  # noqa: F401

pytestmark = pytest.mark.django_db


def post_logo(client, company, **data):
    return client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "set"]), data, follow=True
    )


def test_an_upload_sets_the_logo_and_saves_nothing_else(client, user):
    company = Company.objects.create(owner=user, name="Aperture", notes="keep")
    client.force_login(user)

    response = post_logo(
        client,
        company,
        logo_upload=SimpleUploadedFile("logo.png", an_image(), content_type="image/png"),
    )

    company.refresh_from_db()
    assert company.has_logo and (company.name, company.notes) == ("Aperture", "keep")
    assert response.redirect_chain[-1][0] == reverse("jobs:company_update", args=[company.pk])


def test_an_address_is_fetched_once_and_kept(client, user, web):  # noqa: F811
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    post_logo(client, company, logo_url="https://cdn.example/logo.png")

    company.refresh_from_db()
    assert company.has_logo and company.logo_source_url == "https://cdn.example/logo.png"


def test_nothing_given_is_said_and_a_bad_file_changes_nothing(client, user):
    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    empty = post_logo(client, company)
    bad = post_logo(
        client, company, logo_upload=SimpleUploadedFile("x.png", b"nope", content_type="image/png")
    )

    assert "Give an address or choose a file." in empty.content.decode()
    assert "could not be read as an image" in bad.content.decode()
    company.refresh_from_db()
    assert not company.has_logo


def test_it_is_the_owners_alone(client, user, other_user):
    company = Company.objects.create(owner=other_user, name="Theirs")
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "set"]),
        {"logo_url": "https://cdn.example/logo.png"},
    )

    assert response.status_code == 404


def test_the_edit_page_draws_the_dialog_with_its_three_ways_and_the_create_page_none(
    client,
    user,
    web,  # noqa: F811
):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    company = Company.objects.create(owner=user, name="Aperture", website="https://aperture.test/")
    client.force_login(user)

    edit = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()
    new = client.get(reverse("jobs:company_create")).content.decode()

    assert 'id="logo-dialog"' in edit and 'enctype="multipart/form-data"' in edit
    assert "Find logo" in edit and 'name="logo_url"' in edit and 'name="logo_upload"' in edit
    assert "Remove the logo" not in edit, "nothing to remove yet"
    assert 'id="logo-dialog"' not in new


def test_the_parent_is_drawn_and_a_save_keeps_it(client, user):
    top = Company.objects.create(owner=user, name="Alphabet")
    child = Company.objects.create(owner=user, name="Google", parent=top)
    client.force_login(user)
    url = reverse("jobs:company_update", args=[child.pk])

    page = client.get(url).content.decode()
    client.post(url, {"name": "Google", "parent": top.pk, "kind": child.kind})

    assert 'name="parent"' in page
    child.refresh_from_db()
    assert child.parent_id == top.pk
