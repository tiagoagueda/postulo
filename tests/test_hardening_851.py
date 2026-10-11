"""One company's actions are a button group, and Export offers vCard or JSON (#851)."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from postulo.core import kind_files
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(
        owner=user, name="Aperture", website="https://aperture.test/", notes="private words"
    )


def export(client, company, **query):
    return client.get(reverse("jobs:company_export", args=[company.pk]), query)


def test_the_page_draws_the_group_and_the_export_popover(client, user, company):
    client.force_login(user)

    html = client.get(company.get_absolute_url()).content.decode()

    group = html[html.index("data-company-actions") :].split("</div>")[0]
    for word in ("Edit", "Merge", "Export", "Delete"):
        assert f">{word}</span>" in group, word
    assert group.count('data-icon="') == 4
    assert 'id="export-company"' in html and 'value="json"' in html and 'value="vcard"' in html
    assert "Download the company as a vCard" not in html, "replaced by Export"
    assert "Find logo" not in html and "Refresh logo" not in html


def test_a_vcard_is_the_default_and_keeps_notes_out_unless_asked(client, user, company):
    client.force_login(user)

    plain = export(client, company)
    noted = export(client, company, notes="1")

    assert plain["Content-Type"] == "text/vcard; charset=utf-8"
    assert plain.content.startswith(b"BEGIN:VCARD")
    assert b"private words" not in plain.content and b"private words" in noted.content


def test_the_json_is_the_sets_writer_narrowed_to_this_one_company(client, user, company):
    Company.objects.create(owner=user, name="Black Mesa")
    client.force_login(user)

    response = export(client, company, format="json", notes="1")
    document = json.loads(response.content)

    assert response["Content-Type"].startswith("application/json")
    assert (
        ".json" in response["Content-Disposition"]
        and "attachment" in response["Content-Disposition"]
    )
    assert response["Cache-Control"] == "private, max-age=0, no-store"
    assert [row["name"] for row in document["companies"]] == ["Aperture"]
    assert document["companies"][0]["notes"] == "private words"
    assert document["postulo"]["companies_format"] == kind_files.FORMATS["companies"]


def test_the_json_leaves_the_notes_out_unless_asked_and_keeps_the_key(client, user, company):
    client.force_login(user)

    document = json.loads(export(client, company, format="json").content)

    assert document["companies"][0]["notes"] == ""
    assert "private words" not in json.dumps(document)


def test_it_is_the_owners_alone(client, other_user, company):
    client.force_login(other_user)

    assert export(client, company, format="json").status_code == 404
    assert export(client, company).status_code == 404


def test_the_one_company_file_is_read_back_by_the_set_reader(client, user, company):
    client.force_login(user)
    data = export(client, company, format="json", notes="1").content

    held = kind_files.read("companies", data)

    assert held["rows"][0]["name"] == "Aperture"
