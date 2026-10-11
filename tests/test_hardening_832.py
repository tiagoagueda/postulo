"""The breadcrumb may be a thin line that opens while pointed at (#832)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.urls import reverse

from postulo.core import export, importer
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


def body(client, path) -> str:
    html = client.get(path).content.decode()
    return html[html.index("<body") : html.index(">", html.index("<body")) + 1]


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture")


def test_off_by_default_even_with_the_breadcrumb_on(client, user, company):
    user.profile.show_breadcrumb = True
    user.profile.save()
    client.force_login(user)

    assert "data-breadcrumb-hover" not in body(client, company.get_absolute_url())


def test_on_it_is_stamped_on_the_body(client, user, company):
    user.profile.show_breadcrumb = True
    user.profile.breadcrumb_on_hover = True
    user.profile.save()
    client.force_login(user)

    assert "data-breadcrumb-hover" in body(client, company.get_absolute_url())


def test_it_means_nothing_without_the_breadcrumb(client, user, company):
    user.profile.breadcrumb_on_hover = True
    user.profile.save()
    client.force_login(user)

    assert "data-breadcrumb-hover" not in body(client, company.get_absolute_url())


def test_the_choice_is_offered_only_once_there_is_a_breadcrumb(client, user):
    client.force_login(user)
    assert (
        'name="breadcrumb_on_hover"'
        not in client.get(reverse("settings:accessibility")).content.decode()
    )

    user.profile.show_breadcrumb = True
    user.profile.save()

    assert (
        'name="breadcrumb_on_hover"'
        in client.get(reverse("settings:accessibility")).content.decode()
    )


def test_the_stylesheet_collapses_it_for_a_fine_pointer_and_animates_without_a_preference():
    from pathlib import Path

    css = Path("src/postulo/static/css/app.css").read_text(encoding="utf-8")

    assert "(hover: hover) and (pointer: fine)" in css
    assert "(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)" in css
    assert "body[data-breadcrumb-hover] [data-breadcrumb]" in css


def test_it_travels_in_the_archive(user, other_user):
    user.profile.breadcrumb_on_hover = True
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    other_user.profile.refresh_from_db()
    assert other_user.profile.breadcrumb_on_hover is True
