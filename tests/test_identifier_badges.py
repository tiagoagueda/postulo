"""A company's identifiers as badges, and how they read (#855)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db

LEI = "529900T8BM49AURSDO55"


@pytest.fixture
def company(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value="Q95")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="lei", value=LEI)
    return company


def badges(client, company) -> list[str]:
    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    block = html[html.index("data-identifiers>") :]
    block = block[: block.index("</ul>")]
    return re.findall(r"<li class=\"badge[^\"]*\".*?</li>", block, re.S)


def test_the_default_is_a_badge_with_the_mark_or_icon_the_label_and_the_value(
    client, user, company
):
    client.force_login(user)

    rows = badges(client, company)

    assert len(rows) == 2
    assert all("data-icon=" in row for row in rows), "the scheme's icon where there is no mark"
    for row in rows:
        assert 'class="sr-only"' not in row and "title=" not in row


def test_icon_only_keeps_the_label_for_a_screen_reader_and_as_a_tooltip(client, user, company):
    user.profile.identifier_style = "icon"
    user.profile.save()
    client.force_login(user)

    row = badges(client, company)[1]

    assert "data-icon=" in row
    assert re.search(r'title="[^"]+"', row)
    assert re.search(r'<span class="sr-only">[^<]+</span>', row)
    assert LEI in row


def test_label_only_draws_no_mark_or_icon(client, user, company):
    user.profile.identifier_style = "label"
    user.profile.save()
    client.force_login(user)

    for row in badges(client, company):
        assert "<svg" not in row


def test_the_value_stays_a_link_inside_the_badge(client, user, company):
    client.force_login(user)

    assert re.search(
        r"<a href=\"https://www.wikidata.org/[^\"]*\"[^>]*>Q95</a>",
        badges(client, company)[0],
    )


def test_the_setting_is_on_the_appearance_page_and_is_saved(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    assert 'name="identifier_style"' in page

    client.post(
        reverse("settings:appearance"),
        {
            "theme": "system",
            "density": "comfortable",
            "identifier_style": "label",
            "quiet_after_days": 14,
            "closing_notice_days": 3,
            "navigation": [],
        },
    )
    user.profile.refresh_from_db()
    assert user.profile.identifier_style == "label"


def test_the_setting_travels_in_the_archive_and_a_bad_value_is_refused(user, other_user):
    import io
    import json
    import zipfile

    from postulo.core import export, importer

    user.profile.identifier_style = "icon"
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    assert document["account"]["profile"]["identifier_style"] == "icon"

    def load(value, target):
        document["account"]["profile"]["identifier_style"] = value
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(export.MANIFEST_NAME, json.dumps(document))
        buffer.seek(0)
        importer.load(target, zipfile.ZipFile(buffer))
        target.profile.refresh_from_db()
        return target.profile.identifier_style

    assert load("icon", other_user) == "icon"
    assert load("sparkles", other_user) == "icon", "a value the column refuses is left out"
