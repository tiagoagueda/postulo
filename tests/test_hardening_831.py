"""A breadcrumb under the navigation bar, sticky with it, switched on in Settings (#831)."""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.urls import reverse

from postulo.core import export, importer
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture")


def header(html: str) -> str:
    return html[html.index("data-site-header") : html.index("</header>")]


def test_it_is_off_by_default_and_draws_nothing(client, user, company):
    client.force_login(user)

    html = client.get(company.get_absolute_url()).content.decode()

    assert "data-breadcrumb" not in html


def test_switched_on_it_is_inside_the_header_so_it_sticks_with_it(client, user, company):
    user.profile.show_breadcrumb = True
    user.profile.save()
    client.force_login(user)

    html = client.get(company.get_absolute_url()).content.decode()

    bar = header(html)
    assert "data-breadcrumb" in bar and 'aria-label="Breadcrumb"' in bar
    items = re.findall(r"<li>(.*?)</li>", bar, re.S)
    assert "Companies" in items[0] and reverse("jobs:company_list") in items[0]
    assert 'aria-current="page"' in items[-1] and "Aperture" in items[-1]
    assert 'aria-hidden="true"' in bar, "separators are for the eye, not for a screen reader"
    assert "hidden" in re.search(r'<div class="([^"]*)" data-breadcrumb', bar)[1].split()
    assert "md:block" in bar, "not drawn below md, so the sticky header does not grow"


def test_the_pages_that_fill_it(client, user, company):
    posting = JobPosting.objects.create(owner=user, company=company, title="Tester")
    user.profile.show_breadcrumb = True
    user.profile.save()
    client.force_login(user)

    pages = {
        reverse("jobs:company_update", args=[company.pk]): ("Companies", "Aperture", "Edit"),
        reverse("jobs:company_create"): ("Companies", "New company"),
        posting.get_absolute_url(): ("Listings", "Tester"),
    }
    for path, names in pages.items():
        bar = header(client.get(path).content.decode())
        got = [
            re.sub(r"<[^>]+>|\s+", " ", i).strip() for i in re.findall(r"<li>(.*?)</li>", bar, re.S)
        ]
        assert got == list(names), (path, got)


def test_a_page_with_no_place_in_a_hierarchy_draws_none_even_when_on(client, user):
    user.profile.show_breadcrumb = True
    user.profile.save()
    client.force_login(user)

    assert "data-breadcrumb" not in client.get(reverse("core:home")).content.decode()


def test_the_switch_is_on_the_accessibility_page_and_saved(client, user):
    client.force_login(user)
    assert (
        'name="show_breadcrumb"' in client.get(reverse("settings:accessibility")).content.decode()
    )

    client.post(
        reverse("settings:accessibility"),
        {
            "show_breadcrumb": "on",
            "keyboard_shortcuts": "on",
            "show_key_hints": "on",
            "nav_underline": "on",
        },
    )

    user.profile.refresh_from_db()
    assert user.profile.show_breadcrumb is True


def test_it_travels_in_the_archive(user, other_user):
    user.profile.show_breadcrumb = True
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    assert document["account"]["profile"]["show_breadcrumb"] is True
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    other_user.profile.refresh_from_db()
    assert other_user.profile.show_breadcrumb is True
