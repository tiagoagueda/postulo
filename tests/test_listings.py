"""Listings: the stage before applications, where captured and typed postings wait."""

import datetime as dt
import io
import json
import zipfile

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.core import export as export_module
from postulo.core.export import build_document, write_archive
from postulo.core.importer import load
from postulo.jobs.models import Capture, Company, DiscardReason, JobPosting, ListingState

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science", location="Cambridge")


def listing(user, company, title="Test Engineer", **fields):
    return JobPosting.objects.create(owner=user, company=company, title=title, **fields)


# ------------------------------------------------------------------ the model


def test_a_new_listing_is_undecided_and_earns_its_derived_states(user, company):
    fresh = listing(user, company)
    assert fresh.state == ListingState.NEW
    assert fresh.derived_state == "new"
    assert fresh.is_undecided
    assert fresh.decided_at is None

    fresh.shortlist()
    assert fresh.derived_state == "shortlisted" and fresh.decided_at is not None

    fresh.discard(DiscardReason.PAY)
    assert fresh.derived_state == "discarded" and fresh.discard_reason == DiscardReason.PAY
    assert not fresh.is_undecided

    fresh.restore()
    assert fresh.derived_state == "new" and fresh.decided_at is None

    Application.objects.create(owner=user, posting=fresh, status=Status.APPLIED)
    fresh = JobPosting.objects.with_application_count().get(pk=fresh.pk)
    assert fresh.derived_state == "applied", "derived from the application, never stored"
    assert fresh.state == ListingState.NEW, "the stored state did not have to change"


def test_a_closed_or_expired_listing_is_closed(user, company):
    expired = listing(user, company, closes_at=timezone.localdate() - dt.timedelta(days=1))
    assert expired.derived_state == "closed"
    gone = listing(user, company, title="Gone", closed_at=timezone.now())
    assert gone.derived_state == "closed"
    assert JobPosting.objects.for_user(user).undecided().count() == 0
    assert JobPosting.objects.for_user(user).in_state("closed").count() == 2


def test_the_queryset_filters_agree_with_the_derived_state(user, company):
    new = listing(user, company, title="New")
    short = listing(user, company, title="Short")
    short.shortlist()
    binned = listing(user, company, title="Binned")
    binned.discard()
    applied = listing(user, company, title="Applied")
    Application.objects.create(owner=user, posting=applied, status=Status.APPLIED)
    soon = listing(
        user, company, title="Soon", closes_at=timezone.localdate() + dt.timedelta(days=3)
    )

    everything = JobPosting.objects.for_user(user)
    assert soon.derived_state == "new" and soon.is_undecided
    assert set(everything.undecided().values_list("title", flat=True)) == {"New", "Short", "Soon"}
    assert list(everything.closing_soon().values_list("title", flat=True)) == ["Soon"]
    assert list(everything.in_state("applied").values_list("title", flat=True)) == ["Applied"]
    assert list(everything.in_state("discarded").values_list("title", flat=True)) == ["Binned"]
    assert list(everything.in_state("shortlisted").values_list("title", flat=True)) == ["Short"]
    assert new.derived_state_label == "New"


# ------------------------------------------------------------------- the pages


def test_the_listings_page_shows_what_is_to_decide_by_default(client, user, company):
    listing(user, company, title="Decide me")
    binned = listing(user, company, title="Binned")
    binned.discard(DiscardReason.LOCATION)
    applied = listing(user, company, title="Done")
    Application.objects.create(owner=user, posting=applied, status=Status.APPLIED)
    client.force_login(user)

    html = client.get(reverse("listings:list")).content.decode()
    assert "Decide me" in html and "Binned" not in html and "Done" not in html
    assert 'data-listing-state="new"' in html
    assert reverse("listings:create") in html and reverse("jobs:capture_create") in html

    html = client.get(reverse("listings:list") + "?state=discarded").content.decode()
    assert "Binned" in html and "Decide me" not in html
    html = client.get(reverse("listings:list") + "?state=applied").content.decode()
    assert "Done" in html
    html = client.get(reverse("listings:list") + "?state=nonsense").content.decode()
    assert "Decide me" in html, "an unknown filter falls back to the default"


def test_pending_captures_wait_at_the_top_of_the_listings_page(client, user):
    Capture.objects.create(
        owner=user,
        url="https://example.org/j/1",
        data={"title": "Captured Role", "company_name": "X"},
    )
    client.force_login(user)
    html = client.get(reverse("listings:list")).content.decode()
    assert "captures waiting for review" in html or "capture waiting for review" in html
    assert "Captured Role" in html
    # The old captures page now points here.
    response = client.get(reverse("jobs:capture_list"))
    assert response.status_code == 302 and response.url == reverse("listings:list")


def test_adding_a_listing_by_hand_needs_only_a_company_and_a_title(client, user):
    client.force_login(user)
    response = client.post(
        reverse("listings:create"),
        {"company_name": "Black Mesa", "title": "Research Engineer", "salary_period": "year"},
    )
    assert response.status_code == 302
    created = JobPosting.objects.get(owner=user)
    assert created.company.name == "Black Mesa"
    assert created.derived_state == "new"
    assert response.url == created.get_absolute_url()
    assert not Application.objects.exists()


def test_shortlist_discard_and_restore_from_the_page(client, user, company):
    item = listing(user, company)
    client.force_login(user)
    back = reverse("listings:list") + "?state=shortlisted"

    response = client.post(reverse("listings:shortlist", args=[item.pk]), {"next": back})
    assert response.status_code == 302 and response.url == back
    item.refresh_from_db()
    assert item.state == ListingState.SHORTLISTED

    client.post(reverse("listings:discard", args=[item.pk]), {"reason": "pay"})
    item.refresh_from_db()
    assert item.state == ListingState.DISCARDED and item.discard_reason == DiscardReason.PAY

    client.post(reverse("listings:discard", args=[item.pk]), {"reason": "nonsense"})
    item.refresh_from_db()
    assert item.discard_reason == DiscardReason.OTHER

    response = client.post(reverse("listings:restore", args=[item.pk]), {"next": "https://evil/"})
    assert response.url == reverse("listings:list"), "an offsite next is ignored"
    item.refresh_from_db()
    assert item.state == ListingState.NEW


def test_applying_creates_the_application_and_the_listing_leaves_the_queue(client, user, company):
    item = listing(user, company, title="Apply to me")
    client.force_login(user)
    response = client.get(reverse("listings:apply", args=[item.pk]))
    assert response.status_code == 200
    assert b"Apply: Apply to me" in response.content

    response = client.post(
        reverse("listings:apply", args=[item.pk]),
        {"status": "applied", "priority": "2"},
    )
    assert response.status_code == 302
    application = Application.objects.get(posting=item)
    assert response.url == application.get_absolute_url()
    assert application.status == Status.APPLIED
    assert application.events.filter(summary="Application created").exists()
    item.refresh_from_db()
    assert item.decided_at is not None
    assert JobPosting.objects.for_user(user).undecided().count() == 0
    html = client.get(reverse("listings:list") + "?state=applied").content.decode()
    assert "Apply to me" in html and application.get_absolute_url() in html


def test_the_listing_page_carries_the_decision_buttons(client, user, company):
    item = listing(user, company)
    client.force_login(user)
    html = client.get(item.get_absolute_url()).content.decode()
    assert reverse("listings:shortlist", args=[item.pk]) in html
    assert reverse("listings:discard", args=[item.pk]) in html
    assert reverse("listings:apply", args=[item.pk]) in html
    assert 'name="reason"' in html
    item.discard(DiscardReason.LOCATION)
    html = client.get(item.get_absolute_url()).content.decode()
    assert reverse("listings:restore", args=[item.pk]) in html
    assert "The location" in html


def test_recording_an_application_still_works_in_one_step(client, user):
    client.force_login(user)
    response = client.post(
        reverse("applications:create"),
        {
            "company_name": "Initech",
            "title": "Developer",
            "status": "applied",
            "priority": "2",
            "salary_currency": "EUR",
            "salary_period": "year",
        },
    )
    assert response.status_code == 302
    application = Application.objects.get(owner=user)
    assert application.posting.derived_state == "applied"
    assert application.posting.decided_at is not None


def test_listings_are_private_to_their_owner(client, user, other_user, company):
    item = listing(user, company)
    client.force_login(other_user)
    assert client.get(item.get_absolute_url()).status_code == 404
    assert client.get(reverse("listings:apply", args=[item.pk])).status_code == 404
    assert client.post(reverse("listings:shortlist", args=[item.pk])).status_code == 404
    assert "Test Engineer" not in client.get(reverse("listings:list")).content.decode()


def test_the_navigation_and_the_dashboard_point_at_listings(client, user, company):
    listing(user, company, closes_at=timezone.localdate() + dt.timedelta(days=2))
    listing(user, company, title="Another")
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    header = html[: html.index("</header>")]
    assert reverse("listings:list") in header and "Listings" in header
    assert "2 listings to decide on" in html
    assert "1 closing this week" in html


def test_insights_report_selectivity(client, user, company):
    listing(user, company, title="A")
    binned = listing(user, company, title="B")
    binned.discard()
    applied = listing(user, company, title="C")
    Application.objects.create(owner=user, posting=applied, status=Status.APPLIED)
    client.force_login(user)
    html = client.get(reverse("applications:insights")).content.decode()
    assert "noticed 3 listings" in html
    assert "applied to 1 (33%)" in html
    assert "discarded 1" in html


# -------------------------------------------------------------- export, import


def test_the_export_carries_listing_state_and_the_importer_reads_both_formats(
    user, other_user, company
):
    item = listing(user, company, title="Kept", closes_at=timezone.localdate())
    item.shortlist()
    Capture.objects.create(
        owner=user, url="https://example.org/j/2", data={"title": "Kept"}, posting=item
    )

    document = build_document(user)
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION == 2
    exported = document["companies"][0]["postings"][0]
    assert exported["state"] == "shortlisted" and exported["decided_at"]
    assert document["captures"][0]["posting_id"] == item.pk

    archive_bytes = write_archive(user).getvalue()
    load(other_user, zipfile.ZipFile(io.BytesIO(archive_bytes)))
    restored = JobPosting.objects.get(owner=other_user)
    assert restored.state == ListingState.SHORTLISTED
    assert restored.decided_at is not None
    assert Capture.objects.get(owner=other_user).posting == restored


def test_a_format_one_archive_still_imports(user, other_user, company):
    item = listing(user, company, title="Old style")
    Application.objects.create(owner=user, posting=item, status=Status.APPLIED)
    document = build_document(user)
    # Strip everything format 1 did not have.
    document["postulo"]["format"] = 1
    for company_entry in document["companies"]:
        for posting in company_entry["postings"]:
            for key in ("state", "discard_reason", "noted_at", "decided_at"):
                posting.pop(key, None)
    for capture in document["captures"]:
        capture.pop("posting_id", None)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    restored = JobPosting.objects.get(owner=other_user)
    assert restored.state == ListingState.NEW
    assert restored.noted_at is not None
    assert restored.decided_at is not None, "it had an application, so it was decided"
    assert restored.derived_state == "applied"
