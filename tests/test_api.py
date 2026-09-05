"""The general API: scoped tokens, owner-scoped reads, writes through the services."""

import datetime as dt
import json

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from postulo.api.models import SCOPES, ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.documents.models import CV, CoverLetter, UploadedDocument
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


def issue(user, *scopes, **kwargs):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",), **kwargs)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def patch(client, path, payload, **headers):
    return client.patch(path, data=json.dumps(payload), content_type="application/json", **headers)


@pytest.fixture
def search(user):
    company = Company.objects.create(owner=user, name="Aperture Science", location="Cambridge")
    Contact.objects.create(owner=user, company=company, name="Cave Johnson", role="CEO")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    JobPosting.objects.create(owner=user, company=company, title="Undecided Role")
    return {"company": company, "posting": posting, "application": application}


# --------------------------------------------------------------------- scopes


def test_scopes_are_a_closed_list_and_a_token_holds_a_set(user):
    record, _raw = ApiToken.issue(user, "x", scopes=["read", "captures", "read"])
    assert record.scopes == ["captures", "read"]
    assert record.has_scope("read") and not record.has_scope("write")
    assert set(record.scope_labels) == {str(SCOPES["read"]), str(SCOPES["captures"])}
    with pytest.raises(ValueError, match="Unknown scopes"):
        ApiToken.issue(user, "x", scopes=["everything"])


def test_a_token_without_the_scope_is_told_which_one(client, user):
    bearer = issue(user, "captures")
    response = client.get("/api/v1/applications", **bearer)
    assert response.status_code == 403
    assert "'read' scope" in response.json()["detail"]
    assert client.get("/api/v1/me", **bearer).status_code == 200, "any live token may ask /me"
    assert client.get("/api/v1/me", **bearer).json()["scopes"] == ["captures"]


def test_read_does_not_write_and_write_does_not_download(client, user, search):
    reader = issue(user, "read")
    assert client.get("/api/v1/applications", **reader).status_code == 200
    response = post(
        client, "/api/v1/reminders", {"summary": "x", "due_at": "2030-01-01T09:00:00Z"}, **reader
    )
    assert response.status_code == 403 and "'write'" in response.json()["detail"]

    writer = issue(user, "write")
    assert client.get("/api/v1/applications", **writer).status_code == 403
    upload = UploadedDocument.objects.create(
        owner=user, title="CV", file=ContentFile(b"%PDF-1.4 x", name="cv.pdf")
    )
    response = client.get(f"/api/v1/documents/upload/{upload.pk}/download", **writer)
    assert response.status_code == 403 and "'documents:read'" in response.json()["detail"]


def test_an_expired_token_is_a_stranger(client, user):
    bearer = issue(user, "read", expires_at=timezone.now() - dt.timedelta(minutes=1))
    assert client.get("/api/v1/applications", **bearer).status_code == 401
    record = ApiToken.objects.get(owner=user)
    assert record.is_expired and not record.is_active


def test_existing_tokens_keep_capturing_after_the_migration():
    import importlib

    migration = importlib.import_module("postulo.api.migrations.0002_apitoken_scopes")
    from django.apps import apps
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(email="old@example.org", password="x")
    old = ApiToken.objects.create(
        owner=user, name="old", prefix="abc", token_hash="h" * 64, scopes=[]
    )
    migration.existing_tokens_keep_capturing(apps, None)
    old.refresh_from_db()
    assert old.scopes == ["captures"]


# ---------------------------------------------------------------------- reads


def test_reads_are_the_owners_and_nobody_elses(client, user, other_user, search):
    theirs = Company.objects.create(owner=other_user, name="Black Mesa")
    bearer = issue(user, "read")

    companies = client.get("/api/v1/companies", **bearer).json()
    assert [c["name"] for c in companies["items"]] == ["Aperture Science"]
    assert client.get(f"/api/v1/companies/{theirs.pk}", **bearer).status_code == 404

    applications = client.get("/api/v1/applications", **bearer).json()
    assert applications["count"] == 1
    item = applications["items"][0]
    assert item["status"] == "applied" and item["listing"]["company"]["name"] == "Aperture Science"
    assert item["web_url"].startswith("http://testserver/applications/")

    detail = client.get(f"/api/v1/applications/{search['application'].pk}", **bearer).json()
    assert detail["events"] == [] and detail["reminders"] == []

    company = client.get(f"/api/v1/companies/{search['company'].pk}", **bearer).json()
    assert [c["name"] for c in company["contacts"]] == ["Cave Johnson"]
    assert len(company["listing_ids"]) == 2


def test_listings_default_to_what_is_to_decide(client, user, search):
    bearer = issue(user, "read")
    undecided = client.get("/api/v1/listings", **bearer).json()["items"]
    assert [item["title"] for item in undecided] == ["Undecided Role"]
    assert undecided[0]["state"] == "new"
    everything = client.get("/api/v1/listings?state=all", **bearer).json()["items"]
    assert sorted(item["state"] for item in everything) == ["applied", "new"]
    assert client.get("/api/v1/listings?state=weird", **bearer).status_code == 422


def test_documents_and_insights_read(client, user, search):
    bearer = issue(user, "read", "documents:read")
    cv = CV.objects.create(owner=user, name="Main CV")
    CoverLetter.objects.create(owner=user, name="Letter", body="Dear team")
    upload = UploadedDocument.objects.create(
        owner=user,
        title="Portfolio",
        kind="portfolio",
        file=ContentFile(b"%PDF-1.4 x", name="p.pdf"),
    )
    assert client.get("/api/v1/cvs", **bearer).json()["items"][0]["name"] == "Main CV"
    assert client.get(f"/api/v1/cvs/{cv.pk}", **bearer).json()["items"] == []
    letter = client.get("/api/v1/letters", **bearer).json()["items"][0]
    assert "body" not in letter
    assert client.get(f"/api/v1/letters/{letter['id']}", **bearer).json()["body"] == "Dear team"

    documents = client.get("/api/v1/documents", **bearer).json()["items"]
    assert documents[0]["source"] == "upload" and documents[0]["kind"] == "portfolio"
    download = client.get(documents[0]["download_url"].replace("http://testserver", ""), **bearer)
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == b"%PDF-1.4 x"
    assert (
        client.get(f"/api/v1/documents/upload/{upload.pk + 99}/download", **bearer).status_code
        == 404
    )

    insights = client.get("/api/v1/insights", **bearer).json()
    assert insights["total"] == 1 and insights["listings_noted"] == 2
    assert insights["selectivity"] == 50.0


# --------------------------------------------------------------------- writes


def test_writes_go_through_the_services_and_sign_the_timeline(client, user, search):
    bearer = issue(user, "read", "write")
    application = search["application"]

    response = post(
        client,
        f"/api/v1/applications/{application.pk}/status",
        {"status": "interviewing", "note": "Call on Tuesday"},
        **bearer,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "interviewing"
    event = body["events"][0]
    assert event["kind"] == "status_change" and event["body"] == "Call on Tuesday"
    assert event["actor"] == "API token Agent"

    response = post(
        client,
        f"/api/v1/applications/{application.pk}/events",
        {"kind": "note", "summary": "Sent a thank-you"},
        **bearer,
    )
    assert response.status_code == 201 and response.json()["actor"] == "API token Agent"
    assert (
        post(
            client,
            f"/api/v1/applications/{application.pk}/events",
            {"kind": "status_change"},
            **bearer,
        ).status_code
        == 422
    )
    assert (
        post(
            client, f"/api/v1/applications/{application.pk}/status", {"status": "hired"}, **bearer
        ).status_code
        == 422
    )

    # The timeline page shows who wrote it.
    client.force_login(user)
    html = client.get(application.get_absolute_url()).content.decode()
    assert "via API token Agent" in html


def test_recording_an_application_and_applying_to_a_listing(client, user, search):
    bearer = issue(user, "write", "read")
    response = post(
        client,
        "/api/v1/applications",
        {
            "company_name": "Black Mesa",
            "title": "Research Engineer",
            "status": "applied",
            "tags": ["remote", "dream job"],
        },
        **bearer,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["listing"]["company"]["name"] == "Black Mesa"
    assert sorted(body["tags"]) == ["dream job", "remote"]
    assert [e["actor"] for e in body["events"]] == ["API token Agent", "API token Agent"]

    listing = JobPosting.objects.get(owner=user, title="Undecided Role")
    response = post(client, f"/api/v1/listings/{listing.pk}/shortlist", {}, **bearer)
    assert response.json()["state"] == "shortlisted"
    response = post(client, f"/api/v1/listings/{listing.pk}/apply", {"status": "applied"}, **bearer)
    assert response.status_code == 201
    assert response.json()["listing"]["id"] == listing.pk
    assert client.get(f"/api/v1/listings/{listing.pk}", **bearer).json()["state"] == "applied"

    response = post(
        client, "/api/v1/listings", {"company_name": "Initech", "title": "Developer"}, **bearer
    )
    assert response.status_code == 201 and response.json()["state"] == "new"
    new_id = response.json()["id"]
    assert (
        post(client, f"/api/v1/listings/{new_id}/discard", {"reason": "pay"}, **bearer).json()[
            "discard_reason"
        ]
        == "pay"
    )
    assert (
        post(client, f"/api/v1/listings/{new_id}/discard", {"reason": "meh"}, **bearer).status_code
        == 422
    )
    assert post(client, f"/api/v1/listings/{new_id}/restore", {}, **bearer).json()["state"] == "new"


def test_companies_contacts_reminders_and_letters_write(client, user, search):
    bearer = issue(user, "write", "read")
    response = post(
        client,
        "/api/v1/companies",
        {"name": "aperture science", "website": "https://aperture.example"},
        **bearer,
    )
    assert response.status_code == 201
    assert response.json()["id"] == search["company"].pk, "matched by name, not duplicated"
    assert response.json()["website"] == "https://aperture.example"

    response = patch(
        client,
        f"/api/v1/companies/{search['company'].pk}",
        {"industries": ["Research", "Software"]},
        **bearer,
    )
    assert response.json()["industries"] == ["Research", "Software"]
    assert response.json()["name"] == "Aperture Science"

    response = post(
        client,
        f"/api/v1/companies/{search['company'].pk}/contacts",
        {"name": "Caroline", "role": "Assistant"},
        **bearer,
    )
    assert (
        response.status_code == 201 and Contact.objects.filter(owner=user, name="Caroline").exists()
    )

    due = (timezone.now() + dt.timedelta(days=2)).isoformat()
    response = post(
        client,
        "/api/v1/reminders",
        {"application_id": search["application"].pk, "summary": "Chase", "due_at": due},
        **bearer,
    )
    assert response.status_code == 201
    reminder_id = response.json()["id"]
    assert (
        post(
            client,
            "/api/v1/reminders",
            {"application_id": 999, "summary": "x", "due_at": due},
            **bearer,
        ).status_code
        == 404
    )
    assert client.get("/api/v1/reminders?outstanding=true", **bearer).json()["count"] == 1
    assert post(client, f"/api/v1/reminders/{reminder_id}/complete", {}, **bearer).json()["done_at"]
    assert Reminder.objects.get(pk=reminder_id).is_done

    response = post(
        client, "/api/v1/letters", {"name": "Speculative", "body": "Dear Cave"}, **bearer
    )
    assert (
        response.status_code == 201
        and CoverLetter.objects.filter(owner=user, name="Speculative").exists()
    )


# ------------------------------------------------------------------ the schema


def test_the_openapi_description_is_served_without_a_docs_page(client, db):
    schema = client.get("/api/v1/openapi.json").json()
    assert schema["info"]["title"] == "Postulo API"
    paths = schema["paths"]
    for path in (
        "/api/v1/captures",
        "/api/v1/applications",
        "/api/v1/listings",
        "/api/v1/insights",
    ):
        assert path in paths, path
    assert client.get("/api/v1/docs").status_code == 404


def test_tokens_are_made_with_scopes_and_expiry_from_settings(client, user):
    from django.urls import reverse

    client.force_login(user)
    html = client.get(reverse("api:token_list")).content.decode()
    assert "API tokens" in html and 'name="scopes"' in html and 'name="expires"' in html

    response = client.post(
        reverse("api:token_create"),
        {"name": "Agent", "scopes": ["read", "write"], "expires": "30"},
    )
    assert response.status_code == 302
    token = ApiToken.objects.get(owner=user)
    assert token.scopes == ["read", "write"]
    assert token.expires_at is not None
    assert dt.timedelta(days=29) < token.expires_at - timezone.now() < dt.timedelta(days=31)

    response = client.post(reverse("api:token_create"), {"name": "No scopes"}, follow=True)
    assert "at least one scope" in response.content.decode()
    assert ApiToken.objects.filter(owner=user).count() == 1
