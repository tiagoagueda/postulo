"""A company's social profiles: its LinkedIn page, its accounts, as a contact holds theirs (#906).

The rows are `core.WebLink`, held by the company through a generic relation, edited with the
same block a contact's are, and carried by the API and the archive. A company is offered the
social kind alone: its website stays the column it has always been, so a document still
prints one, and a code repository is not something a company's page lists.
"""

from __future__ import annotations

import json
import zipfile

import pytest
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.core import export as export_module
from postulo.core import importer, web_links
from postulo.core.models import WebLink
from postulo.jobs import merging
from postulo.jobs.models import Company

from .test_link_services import rows

pytestmark = pytest.mark.django_db


def a_company(user, name="Aperture Science"):
    return Company.objects.create(owner=user, name=name, website="https://aperture.example")


def profile(company, user, url, *, service="", primary=False):
    return WebLink.objects.create(
        owner=user,
        holder=company,
        kind=web_links.Kind.SOCIAL,
        service=service,
        url=url,
        is_primary=primary,
    )


def company_post(company, **extra):
    return {"name": company.name, "kind": company.kind, "website": company.website, **extra}


# ------------------------------------------------------------------- the form


def test_the_company_form_offers_social_profiles_and_no_other_kind(client, user):
    company = a_company(user)
    client.force_login(user)
    html = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    assert 'data-web-links="social"' in html
    assert 'data-web-links="repository"' not in html and 'data-web-links="website"' not in html
    assert "The company&#x27;s own pages" in html or "The company's own pages" in html


def test_the_form_saves_the_rows_with_their_services(client, user):
    company = a_company(user)
    client.force_login(user)
    posted = rows(
        "social_profiles",
        {"service": "linkedin", "label": "", "url": "https://www.linkedin.com/company/aperture"},
        {"service": "", "label": "", "url": "https://mastodon.social/@aperture"},
    )

    response = client.post(
        reverse("jobs:company_update", args=[company.pk]), company_post(company, **posted)
    )

    assert response.status_code == 302, response.content.decode()[:800]
    saved = {(row.kind, row.service, row.url) for row in company.web_links.all()}
    assert saved == {
        ("social", "linkedin", "https://www.linkedin.com/company/aperture"),
        ("social", "mastodon", "https://mastodon.social/@aperture"),
    }, "an address with no service chosen chooses its own"
    assert company.web_links.filter(is_primary=True).count() == 1
    company.refresh_from_db()
    assert company.website == "https://aperture.example", "the website stays its column"


def test_a_new_company_takes_its_rows_with_it(client, user):
    client.force_login(user)
    posted = rows(
        "social_profiles",
        {"service": "bluesky", "label": "", "url": "https://bsky.app/profile/black-mesa.example"},
    )
    response = client.post(
        reverse("jobs:company_create"), {"name": "Black Mesa", "kind": "employer", **posted}
    )
    assert response.status_code == 302, response.content.decode()[:800]
    company = Company.objects.get(owner=user, name="Black Mesa")
    assert [row.service for row in company.web_links.all()] == ["bluesky"]


def test_a_refused_row_refuses_the_whole_form_and_saves_nothing(client, user):
    client.force_login(user)
    posted = rows(
        "social_profiles", {"service": "linkedin", "label": "", "url": "https://example.org/x"}
    )
    response = client.post(
        reverse("jobs:company_create"), {"name": "Black Mesa", "kind": "employer", **posted}
    )
    assert response.status_code == 200
    assert not Company.objects.filter(owner=user, name="Black Mesa").exists()


def test_while_several_are_off_the_form_is_one_box(user, monkeypatch):
    from postulo.jobs.forms import CompanyForm

    monkeypatch.setattr(web_links, "several_allowed", lambda person, kind: False)
    company = a_company(user)
    profile(company, user, "https://www.linkedin.com/company/aperture", primary=True)

    form = CompanyForm(user=user, instance=company)
    assert [box.name for box in form.link_boxes] == ["social_profile"]
    assert form["social_profile"].initial == "https://www.linkedin.com/company/aperture"
    assert "website" in form.fields, "the company's own column, untouched"


# ------------------------------------------------------------------- the page


def test_the_company_page_lists_them_under_the_website_with_their_marks(client, user):
    company = a_company(user)
    profile(company, user, "https://fosstodon.org/@aperture", service="mastodon", primary=True)
    profile(company, user, "https://forum.example.org/aperture")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    block = html.split("data-company-links", 1)[1].split("</ul>", 1)[0]
    assert 'data-brand="mastodon"' in block
    assert 'href="https://fosstodon.org/@aperture"' in block
    assert 'href="https://forum.example.org/aperture"' in block
    assert html.index("data-company-links") > html.index(">Website<")


# ------------------------------------------------------------------- API


def api(client, user, scopes=("read", "write")):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_reads_and_writes_a_companys_profiles(client, user):
    headers = api(client, user)
    created = client.post(
        "/api/v1/companies",
        data=json.dumps(
            {
                "name": "Aperture",
                "web_links": [
                    {"kind": "social", "url": "https://www.linkedin.com/company/aperture"}
                ],
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert [(r["service"], r["is_primary"]) for r in body["web_links"]] == [("linkedin", True)]

    patched = client.patch(
        f"/api/v1/companies/{body['id']}",
        data=json.dumps(
            {"web_links": [{"kind": "social", "url": "https://fosstodon.org/@aperture"}]}
        ),
        content_type="application/json",
        **headers,
    )
    assert [r["url"] for r in patched.json()["web_links"]] == ["https://fosstodon.org/@aperture"]

    kept = client.patch(
        f"/api/v1/companies/{body['id']}",
        data=json.dumps({"notes": "x"}),
        content_type="application/json",
        **headers,
    )
    assert len(kept.json()["web_links"]) == 1, "left out means unchanged"


def test_the_api_refuses_another_kind_and_a_wrong_service(client, user):
    headers = api(client, user)
    for row in (
        {"kind": "website", "url": "https://aperture.example"},
        {"kind": "social", "service": "linkedin", "url": "https://example.org/x"},
    ):
        response = client.post(
            "/api/v1/companies",
            data=json.dumps({"name": "Aperture", "web_links": [row]}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 422, row
    assert not Company.objects.filter(owner=user).exists(), "a refusal leaves nothing behind"


# ------------------------------------------------------------------- archive and merge


def test_the_archive_carries_them_and_an_old_website_key_is_not_a_link(user, other_user):
    company = a_company(user)
    profile(company, user, "https://fosstodon.org/@aperture", service="mastodon", primary=True)

    document = export_module.build_document(user)
    entry = next(c for c in document["companies"] if c["name"] == "Aperture Science")
    assert [row["url"] for row in entry["web_links"]] == ["https://fosstodon.org/@aperture"]
    assert export_module.FORMAT_VERSION >= 71

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))
    restored = Company.objects.get(owner=other_user, name="Aperture Science")
    assert [(r.service, r.url) for r in restored.web_links.all()] == [
        ("mastodon", "https://fosstodon.org/@aperture")
    ]
    assert restored.website == "https://aperture.example"


def test_an_archive_row_of_another_kind_is_left_out():
    entry = {
        "web_links": [
            {"kind": "social", "url": "https://fosstodon.org/@a"},
            {"kind": "website", "url": "https://a.example"},
            {"kind": "social", "url": " "},
            "nonsense",
        ],
        "website": "https://a.example",
    }
    found = importer._company_link_rows(entry)
    assert [row["url"] for row in found] == ["https://fosstodon.org/@a"]
    assert "web_links" not in entry and entry["website"] == "https://a.example"


def test_a_merge_moves_them_and_does_not_list_one_twice(user):
    kept = a_company(user)
    other = a_company(user, "Aperture Labs")
    profile(kept, user, "https://fosstodon.org/@aperture", primary=True)
    profile(other, user, "https://fosstodon.org/@aperture", primary=True)
    profile(other, user, "https://bsky.app/profile/aperture.example")

    plan = merging.merge_companies(kept, other)

    urls = sorted(row.url for row in kept.web_links.all())
    assert urls == ["https://bsky.app/profile/aperture.example", "https://fosstodon.org/@aperture"]
    assert kept.web_links.filter(is_primary=True).count() == 1
    assert any(str(line.label) == "Social profiles" for line in plan.moves)
    assert not WebLink.objects.filter(object_id=other.pk, content_type__model="company").exists()


def test_deleting_a_company_deletes_its_profiles(user):
    company = a_company(user)
    profile(company, user, "https://fosstodon.org/@aperture", primary=True)
    pk = company.pk
    company.delete()
    assert not WebLink.objects.filter(object_id=pk, content_type__model="company").exists()
