"""The general API: scoped tokens, owner-scoped reads, writes through the services."""

import datetime as dt
import json

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from postulo.api.models import SCOPES, ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.documents.models import CV, CoverLetter, UploadedDocument
from postulo.jobs.models import Company, Contact, Industry, JobPosting

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


def test_bind_files_into_a_listing_and_reads_nothing_of_the_search(client, user, search):
    """A mail client's scope (#270): the brief list and one entry, and every read refused.

    `tests/security/test_listing_history.py` walks the rest of the API with it; this is the
    same promise stated where the other scopes' are.
    """
    binder = issue(user, "listings:bind")
    listing = search["posting"]

    choices = client.get("/api/v1/listings/choices", **binder)
    made = post(client, f"/api/v1/listings/{listing.pk}/events", {"summary": "x"}, **binder)
    reading = client.get("/api/v1/listings", **binder)

    assert choices.status_code == 200 and choices.json()["count"] == 2
    assert made.status_code == 201 and made.json()["actor"] == "API token Agent"
    assert reading.status_code == 403 and "'read'" in reading.json()["detail"]
    assert client.get("/api/v1/listings/choices", **issue(user, "read")).status_code == 200
    assert client.get("/api/v1/listings/choices", **issue(user, "write")).status_code == 403


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

    german = CoverLetter.objects.create(owner=user, name="Brief", body="Hallo", language="de")
    assert client.get(f"/api/v1/letters/{german.pk}", **bearer).json()["language"] == "de"
    listed = {row["id"]: row for row in client.get("/api/v1/letters", **bearer).json()["items"]}
    assert listed[german.pk]["language"] == "de"
    assert listed[letter["id"]]["language"] == ""

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


def test_an_upload_downloads_under_its_own_extension_over_the_api(client, user):
    """The API named every upload `<title>.pdf` as well (#193)."""
    bearer = issue(user, "read", "documents:read")
    upload = UploadedDocument.objects.create(
        owner=user, title="Portfolio", kind="portfolio", file=ContentFile(b"PK", name="p.docx")
    )
    download = client.get(f"/api/v1/documents/upload/{upload.pk}/download", **bearer)
    try:
        assert download.status_code == 200
        assert 'filename="Portfolio.docx"' in download["Content-Disposition"]
        assert not download["Content-Type"].startswith("application/pdf")
    finally:
        download.close()


def test_a_sent_document_downloads_over_the_api_for_its_owner_only(client, user, other_user):
    """The `rendered` branch of the download, which nothing asked for before (#422)."""
    from postulo.documents.models import RenderedDocument

    bearer = issue(user, "read", "documents:read")
    mine = RenderedDocument.objects.create(
        owner=user,
        title="My sent CV",
        kind="cv",
        source=CV.objects.create(owner=user, name="Main CV"),
        file=ContentFile(b"%PDF-1.7 mine", name="sent.pdf"),
        checksum="mine",
    )

    download = client.get(f"/api/v1/documents/rendered/{mine.pk}/download", **bearer)
    try:
        assert download.status_code == 200
        assert b"".join(download.streaming_content) == b"%PDF-1.7 mine"
        assert "attachment" in download["Content-Disposition"]
    finally:
        download.close()

    listed = client.get("/api/v1/documents", **bearer).json()["items"]
    assert [row["source"] for row in listed] == ["rendered"]

    theirs = issue(other_user, "read", "documents:read")
    assert client.get(f"/api/v1/documents/rendered/{mine.pk}/download", **theirs).status_code == 404


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


def test_tags_that_shared_a_slug_are_accepted_and_kept(client, user):
    from postulo.core.models import Tag

    Tag.objects.create(owner=user, name="remote")
    bearer = issue(user, "write", "read")
    for tags in (["Remote!"], ["C++", "C#"], ["x" * 70], ["Κάτι", "Срочно"]):
        response = post(
            client,
            "/api/v1/applications",
            {"company_name": "Black Mesa", "title": "Engineer", "tags": tags},
            **bearer,
        )
        assert response.status_code == 201, tags
        assert len(response.json()["tags"]) == len(tags)
    assert Tag.objects.filter(owner=user, name="x" * 60).count() == 1


def test_a_company_posted_with_a_greek_industry_has_it(client, user):
    bearer = issue(user, "write", "read")

    response = post(
        client, "/api/v1/companies", {"name": "Aperture", "industries": ["Πληροφορική"]}, **bearer
    )

    assert response.status_code == 201
    assert response.json()["industries"] == ["Πληροφορική"]


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


def test_renaming_a_company_follows_the_forms_rules(client, user, search):
    """Another company's name in any case and an empty name are a 422, never a 500 (#546)."""
    bearer = issue(user, "write", "read")
    path = f"/api/v1/companies/{search['company'].pk}"
    Company.objects.create(owner=user, name="Black Mesa")

    assert patch(client, path, {"name": "BLACK MESA"}, **bearer).status_code == 422
    assert patch(client, path, {"name": "Black Mesa"}, **bearer).status_code == 422
    assert patch(client, path, {"name": "  "}, **bearer).status_code == 422
    search["company"].refresh_from_db()
    assert search["company"].name == "Aperture Science"

    assert patch(client, path, {"name": "APERTURE SCIENCE"}, **bearer).status_code == 200, (
        "its own name in another case is a rename, not a clash"
    )


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


def test_the_openapi_description_is_served_without_a_docs_page(client, user):
    schema = client.get("/api/v1/openapi.json", **issue(user, "captures")).json()
    assert schema["info"]["title"] == "Postulo API"
    paths = schema["paths"]
    for path in (
        "/api/v1/captures",
        "/api/v1/applications",
        "/api/v1/listings",
        "/api/v1/insights",
    ):
        assert path in paths, path
    assert client.get("/api/v1/docs", **issue(user, "captures")).status_code == 404


def test_the_schema_answers_a_token_or_a_person_and_nobody_else(client, user):
    """It answered anybody, against the promise that the API answers 401 without one (#230).

    Any live token will do, whatever its scopes: the schema describes calls a token may not
    make, and refusing to say what a call is called is not what the scopes are for.
    """
    # Two refusals are two requests; compared, they are given the same id (#393).
    same = {"X-Request-ID": "one-and-the-same"}
    anonymous = client.get("/api/v1/openapi.json", headers=same)
    assert anonymous.status_code == 401
    # The same refusal as everything else -- compared against a real one rather than
    # written out here, because this view is guarded by hand and nothing else would notice
    # it drifting out of the shape the API answers with (#296).
    elsewhere = client.get("/api/v1/applications", headers=same)
    assert elsewhere.status_code == 401
    assert anonymous.json() == {**elsewhere.json(), "instance": "/api/v1/openapi.json"}
    assert anonymous["Content-Type"] == elsewhere["Content-Type"] == "application/problem+json"
    assert (
        client.get("/api/v1/openapi.json", HTTP_AUTHORIZATION="Bearer nonsense").status_code == 401
    )

    expired = issue(user, "read", expires_at=timezone.now() - dt.timedelta(minutes=1))
    assert client.get("/api/v1/openapi.json", **expired).status_code == 401

    assert client.get("/api/v1/openapi.json", **issue(user, "read")).status_code == 200

    client.force_login(user)
    assert client.get("/api/v1/openapi.json").status_code == 200, "a signed-in person may read it"


def test_reading_the_schema_is_not_using_the_api(client, user):
    """It spends no allowance and marks no token as used: asking what there is is not a call."""
    record, raw = ApiToken.issue(user, "Agent", scopes=("read",))

    assert client.get("/api/v1/openapi.json", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code == 200

    record.refresh_from_db()
    assert record.last_used_at is None


def test_tokens_are_made_with_scopes_and_expiry_from_settings(client, user):
    from django.urls import reverse

    client.force_login(user)
    html = client.get(reverse("api:token_list")).content.decode()
    assert "API tokens" in html and 'name="scopes"' in html and 'name="expires"' in html

    response = client.post(
        reverse("api:token_create"),
        {"name": "Agent", "scopes": ["read", "write"], "expires": "30"},
    )
    assert response.status_code == 200, "the list, drawn by the response that made the token"
    token = ApiToken.objects.get(owner=user)
    assert token.scopes == ["read", "write"]
    assert token.expires_at is not None
    assert dt.timedelta(days=29) < token.expires_at - timezone.now() < dt.timedelta(days=31)

    response = client.post(reverse("api:token_create"), {"name": "No scopes"}, follow=True)
    assert "at least one scope" in response.content.decode()
    assert ApiToken.objects.filter(owner=user).count() == 1


def test_blank_names_are_refused_and_nothing_is_made(client, user, search):
    bearer = issue(user, "write", "read")
    company = search["company"]
    calls = [
        ("/api/v1/companies", {"name": "   "}, "name"),
        ("/api/v1/listings", {"company_name": " ", "title": "Tester"}, "company_name"),
        ("/api/v1/listings", {"company_name": "Acme", "title": ""}, "title"),
        ("/api/v1/applications", {"company_name": "", "title": "  "}, "company_name"),
        (f"/api/v1/companies/{company.pk}/contacts", {"name": ""}, "name"),
        ("/api/v1/reminders", {"summary": " ", "due_at": "2030-01-01T00:00:00Z"}, "summary"),
        ("/api/v1/letters", {"name": "", "body": "x"}, "name"),
    ]
    before = Company.objects.count()
    for path, payload, field in calls:
        response = post(client, path, payload, **bearer)
        assert response.status_code == 422, path
        assert field in json.dumps(response.json()), path
    assert Company.objects.count() == before
    assert not Company.objects.filter(name="").exists()
    assert not Contact.objects.filter(name="").exists()

    for payload in ({"name": "  "}, {"name": ""}):
        response = patch(client, f"/api/v1/companies/{company.pk}", payload, **bearer)
        assert response.status_code == 422
        company.refresh_from_db()
        assert company.name == "Aperture Science"


def test_renaming_a_company_onto_a_taken_name_is_refused(client, user, search):
    bearer = issue(user, "write", "read")
    other = Company.objects.create(owner=user, name="Other")
    for name in ("Other", "other", " OTHER "):
        response = patch(
            client, f"/api/v1/companies/{search['company'].pk}", {"name": name}, **bearer
        )
        assert response.status_code == 422
    search["company"].refresh_from_db()
    other.refresh_from_db()
    assert search["company"].name == "Aperture Science" and other.name == "Other"
    # Keeping or recasing its own name is fine.
    response = patch(
        client, f"/api/v1/companies/{search['company'].pk}", {"name": "APERTURE science"}, **bearer
    )
    assert response.status_code == 200


def test_a_listing_currency_is_checked_and_stored_in_capitals(client, user, search):
    """The listing endpoint held the currency to three characters only (#446)."""
    bearer = issue(user, "write", "read")
    before = JobPosting.objects.count()
    refused = post(
        client,
        "/api/v1/listings",
        {"company_name": "Initech", "title": "Dev", "salary_currency": "eu1"},
        **bearer,
    )
    assert refused.status_code == 422
    assert "salary_currency" in json.dumps(refused.json())
    assert JobPosting.objects.count() == before

    made = post(
        client,
        "/api/v1/listings",
        {"company_name": "Initech", "title": "Dev", "salary_currency": "eur"},
        **bearer,
    )
    assert made.status_code == 201
    assert JobPosting.objects.get(pk=made.json()["id"]).salary_currency == "EUR"


def test_a_time_without_an_offset_is_refused_naming_the_field(client, user, search):
    """A naive time was a 500 on interviews and a shifted reminder elsewhere (#567)."""
    bearer = issue(user, "write", "read")
    application_id = search["application"].pk
    naive = "2026-11-02T10:00:00"

    response = post(
        client,
        "/api/v1/interviews",
        {"application_id": application_id, "kind": "video", "starts_at": naive},
        **bearer,
    )
    assert response.status_code == 422
    assert response["Content-Type"].startswith("application/problem+json")
    assert "starts_at" in response.content.decode()

    made = post(
        client,
        "/api/v1/interviews",
        {
            "application_id": application_id,
            "kind": "video",
            "starts_at": "2026-11-02T10:00:00Z",
        },
        **bearer,
    )
    assert made.status_code == 201
    response = patch(
        client, f"/api/v1/interviews/{made.json()['id']}", {"starts_at": naive}, **bearer
    )
    assert response.status_code == 422 and "starts_at" in response.content.decode()

    response = post(
        client,
        "/api/v1/reminders",
        {"application_id": application_id, "summary": "Chase", "due_at": naive},
        **bearer,
    )
    assert response.status_code == 422 and "due_at" in response.content.decode()
    assert not Reminder.objects.filter(summary="Chase").exists()

    assert client.get("/api/v1/applications?updated_since=" + naive, **bearer).status_code == 422


def test_me_says_the_owners_time_zone(client, user):
    user.profile.time_zone = "America/New_York"
    user.profile.save()
    assert client.get("/api/v1/me", **issue(user)).json()["time_zone"] == "America/New_York"


def test_a_refused_company_or_interview_write_keeps_nothing(client, user, search):
    """The check that refuses came after the saves, and the request committed them (#435)."""
    from postulo.applications.models import Interview, InterviewKind
    from postulo.applications.services import schedule_interview

    bearer = issue(user, "write", "read")
    company = search["company"]
    bad = [{"scheme": "lei", "value": "not-a-lei"}]

    response = post(
        client,
        "/api/v1/companies",
        {"name": "Mesa", "website": "https://mesa.example", "identifiers": bad},
        **bearer,
    )
    assert response.status_code == 422
    assert not Company.objects.filter(owner=user, name="Mesa").exists()

    response = post(
        client,
        "/api/v1/companies",
        {"name": "aperture science", "website": "https://changed.example", "identifiers": bad},
        **bearer,
    )
    assert response.status_code == 422
    company.refresh_from_db()
    assert company.website != "https://changed.example"

    company.industries.add(*Industry.named(user, ["Research"]))
    response = patch(
        client,
        f"/api/v1/companies/{company.pk}",
        {"name": "Renamed", "industries": ["Software"], "identifiers": bad},
        **bearer,
    )
    assert response.status_code == 422
    company.refresh_from_db()
    assert company.name == "Aperture Science"
    assert [i.name for i in company.industries.all()] == ["Research"]

    interview = schedule_interview(
        search["application"],
        kind=InterviewKind.VIDEO,
        starts_at=timezone.now() + dt.timedelta(days=3),
        location="Room 1",
    )
    stranger = Contact.objects.create(
        owner=user, company=Company.objects.create(owner=user, name="Elsewhere"), name="Chell"
    )
    response = patch(
        client,
        f"/api/v1/interviews/{interview.pk}",
        {"location": "Moved", "contact_ids": [stranger.pk]},
        **bearer,
    )
    assert response.status_code == 422
    assert Interview.objects.get(pk=interview.pk).location == "Room 1"


def test_the_listing_detail_and_writes_carry_the_description_format(client, user):
    bearer = issue(user, "read", "write")
    made = post(
        client,
        "/api/v1/listings",
        {
            "company_name": "Initech",
            "title": "Developer",
            "description": "- **a**",
            "description_format": "markdown",
        },
        **bearer,
    )
    assert made.status_code in (200, 201)
    listing_id = made.json()["id"]
    detail = client.get(f"/api/v1/listings/{listing_id}", **bearer).json()
    # The source as it was stored, never markup.
    assert detail["description"] == "- **a**" and detail["description_format"] == "markdown"
    assert "<" not in detail["description"]

    plain = post(client, "/api/v1/listings", {"company_name": "Initech", "title": "Two"}, **bearer)
    assert (
        client.get(f"/api/v1/listings/{plain.json()['id']}", **bearer).json()["description_format"]
        == "plain"
    )
    refused = post(
        client,
        "/api/v1/listings",
        {"company_name": "Initech", "title": "Three", "description_format": "html"},
        **bearer,
    )
    assert refused.status_code == 422


def test_the_token_list_names_scopes_as_sentences_and_translates_the_placeholder(
    client, user, german
):
    from django.urls import reverse
    from django.utils.functional import Promise
    from django.utils.html import escape

    from postulo.api.forms import ApiTokenForm

    ApiToken.issue(user, "Agent", scopes=("read", "captures"))
    user.profile.language = "de"
    user.profile.save()
    client.force_login(user)
    entries = {
        str(SCOPES["read"]): "Alles lesen",
        str(SCOPES["captures"]): "Stellen erfassen",
        "Firefox on the laptop": "Firefox auf dem Laptop",
    }
    with german(entries):
        html = client.get(reverse("api:token_list")).content.decode()
    assert "Alles lesen <code>read</code>" in html
    assert "Stellen erfassen <code>captures</code>" in html
    assert escape(str(SCOPES["read"])) not in html and "captures, read" not in html
    assert 'placeholder="Firefox auf dem Laptop"' in html
    assert isinstance(ApiTokenForm().fields["name"].widget.attrs["placeholder"], Promise)
