"""A contact's own page, and everything that happened with them (#1097).

A contact had a form and nothing else: before calling a recruiter there was no one place to
see which applications they were on, which interviews they were at, what they sent and
which letters they wrote. The page reads every row that names the contact by a foreign key,
the reader's own only, in a fixed number of queries however long the history is.
"""

import datetime as dt

import pytest
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Appointment, Interview
from postulo.documents.models import ReferenceLetter, UploadedDocument
from postulo.jobs.models import Company, Contact, JobPosting, ListingEvent, Share
from postulo.resume.models import Reference

pytestmark = pytest.mark.django_db


def page(contact) -> str:
    return reverse("jobs:contact_detail", args=[contact.pk])


def a_contact(owner, name="Cave Johnson") -> Contact:
    company = Company.objects.create(owner=owner, name="Aperture")
    return Contact.objects.create(owner=owner, company=company, name=name, role="Recruiter")


def history_with(contact, owner, *, each: int, mark: str) -> None:
    """``each`` rows of every kind that names ``contact``, all ``owner``'s."""
    company = Company.objects.create(owner=owner, name=f"Black Mesa {mark}")
    now = timezone.now()
    for index in range(each):
        label = f"{mark}{index}"
        posting = JobPosting.objects.create(owner=owner, company=company, title=f"Role {label}")
        main = Application.objects.create(owner=owner, posting=posting, contact=contact)
        Application.objects.create(owner=owner, posting=posting, referred_by=contact)
        starts = now - dt.timedelta(days=10 + index)
        interview = Interview.objects.create(
            owner=owner, application=main, starts_at=starts, ends_at=starts + dt.timedelta(hours=1)
        )
        interview.contacts.set([contact])
        Appointment.objects.create(
            owner=owner, company=company, contact=contact, starts_at=starts, ends_at=None
        )
        ListingEvent.objects.create(
            posting=posting, owner=owner, summary=f"Rang {label}", contact=contact
        )
        upload = UploadedDocument.objects.create(
            owner=owner,
            title=f"Letter {label}",
            kind="reference",
            file=ContentFile(b"%PDF-1.7", name=f"{label}.pdf"),
        )
        ReferenceLetter.objects.create(
            owner=owner, upload=upload, referee=contact, written_on=dt.date(2026, 3, 1)
        )


def test_the_page_draws_every_row_that_names_the_contact_newest_first(client, user):
    """Each kind of row is on the page, linked to where it belongs, the latest first (#1097)."""
    contact = a_contact(user)
    history_with(contact, user, each=1, mark="a")
    Reference.objects.create(owner=user, contact=contact)
    client.force_login(user)

    response = client.get(page(contact))

    assert response.status_code == 200
    kinds = [entry.kind for entry in response.context["history"]]
    assert sorted(kinds) == sorted(
        ["application", "referral", "interview", "appointment", "listing_event", "reference_letter"]
    )
    moments = [entry.order for entry in response.context["history"]]
    assert moments == sorted(moments, reverse=True)
    body = response.content.decode()
    application = Application.objects.get(contact=contact)
    assert application.get_absolute_url() in body
    assert f"{application.posting.get_absolute_url()}#history" in body
    assert "Rang a0" in body
    assert "Letter a0" in body
    assert "data-career-reference" in body


def test_a_contact_with_no_history_says_so(client, user):
    contact = a_contact(user)
    client.force_login(user)

    response = client.get(page(contact))

    assert response.status_code == 200
    assert response.context["history"] == []
    assert "Nothing recorded with them yet." in response.content.decode()


def test_a_shared_contact_shows_each_reader_only_their_own_history(client, user, other_user):
    """A recruiter shared with a colleague is one row and two histories (#896): the
    colleague sees their own interviews with them and never the owner's (#1097)."""
    contact = a_contact(user)
    Share.objects.create(contact=contact, grantee_user=other_user, scope=Share.Scope.READ)
    history_with(contact, user, each=2, mark="mine")
    company = Company.objects.create(owner=other_user, name="Their own")
    posting = JobPosting.objects.create(owner=other_user, company=company, title="Their role")
    theirs = Application.objects.create(owner=other_user, posting=posting, contact=contact)
    client.force_login(other_user)

    response = client.get(page(contact))

    assert response.status_code == 200
    assert [entry.url for entry in response.context["history"]] == [theirs.get_absolute_url()]
    assert "Role mine0" not in response.content.decode()


def test_somebody_elses_contact_is_not_found(client, user, other_user):
    contact = a_contact(other_user)
    client.force_login(user)

    assert client.get(page(contact)).status_code == 404


def test_the_page_costs_the_same_with_one_row_of_each_kind_and_five(
    client, user, django_assert_num_queries
):
    """One query per kind of row, never one per row (#1097)."""
    contact = a_contact(user)
    client.force_login(user)
    history_with(contact, user, each=1, mark="a")
    # Asked once first, as the CV's budget is: the first request also writes what is
    # written once per session, which is not the page's cost.
    assert client.get(page(contact)).status_code == 200
    with CaptureQueriesContext(connection) as small:
        assert client.get(page(contact)).status_code == 200

    history_with(contact, user, each=4, mark="b")
    with django_assert_num_queries(len(small.captured_queries)):
        response = client.get(page(contact))
    assert len(response.context["history"]) == 5 * 6


def test_the_company_the_application_and_search_link_to_the_page(client, user):
    """Where a contact is named, their name leads to their page rather than the form."""
    contact = a_contact(user)
    posting = JobPosting.objects.create(owner=user, company=contact.company, title="Engineer")
    application = Application.objects.create(
        owner=user, posting=posting, contact=contact, referred_by=contact
    )
    client.force_login(user)
    url = page(contact)

    assert url in client.get(contact.company.get_absolute_url()).content.decode()
    assert client.get(application.get_absolute_url()).content.decode().count(f'href="{url}"') == 2
    assert contact.get_absolute_url() == url
    from postulo.core.search import search_contacts

    hits = search_contacts(user, "Cave", 5)
    assert [hit.url for hit in hits.hits] == [url]
