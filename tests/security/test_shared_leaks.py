"""Nothing private comes along with a shared record (#896).

The owner shares a company and a listing. Around them they keep things of their own -- a
note, a decision, an application, a private contact at the same company, a history entry --
and none of it may reach the person the records were shared with, through any surface:
the pages, the API, the search, the map, the vCard, the export.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Appointment, EventKind, Status
from postulo.applications.services import create_listing, record_event
from postulo.jobs.history import record_listing_event
from postulo.jobs.models import Company, Contact, JobPosting, Share

pytestmark = pytest.mark.django_db

SECRETS = (
    "OWNER-NOTE-ON-COMPANY",
    "OWNER-PRIVATE-CONTACT",
    "OWNER-NOTE-ON-CONTACT",
    "OWNER-HISTORY-ENTRY",
    "OWNER-APPLICATION-NOTE",
    "OWNER-APPOINTMENT-NOTE",
)


@pytest.fixture
def world(user, other_user):
    """``other_user`` owns everything; ``user`` is given the company and the listing."""
    owner, grantee = other_user, user
    company = Company.objects.create(owner=owner, name="Shared Co")
    company.write_note(owner, "OWNER-NOTE-ON-COMPANY")
    hidden = Contact.objects.create(owner=owner, company=company, name="OWNER-PRIVATE-CONTACT")
    hidden.write_note(owner, "OWNER-NOTE-ON-CONTACT")
    posting = create_listing(owner, company=company, posting_data={"title": "Shared role"})
    posting.discard(owner, "pay")
    record_listing_event(posting, summary="OWNER-HISTORY-ENTRY", owner=owner)
    application = Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)
    record_event(application, kind=EventKind.NOTE, summary="OWNER-APPLICATION-NOTE")
    Appointment.objects.create(
        owner=owner, company=company, starts_at=timezone.now(), notes="OWNER-APPOINTMENT-NOTE"
    )
    Share.objects.create(company=company, grantee_user=grantee, scope=Share.Scope.WRITE)
    Share.objects.create(posting=posting, grantee_user=grantee, scope=Share.Scope.WRITE)
    return {"company": company, "posting": posting, "application": application}


def assert_clean(text: str, where: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{where} shows {secret}"


def test_the_pages_show_nothing_of_the_owner_s(client, user, world):
    client.force_login(user)
    company, posting, application = world["company"], world["posting"], world["application"]
    for url in (
        reverse("jobs:company_detail", args=[company.pk]),
        reverse("jobs:company_update", args=[company.pk]),
        reverse("jobs:posting_detail", args=[posting.pk]),
        reverse("listings:list") + "?state=all",
        reverse("jobs:company_list"),
    ):
        page = client.get(url)
        assert page.status_code == 200, url
        text = page.content.decode()
        assert_clean(text, url)
        assert reverse("applications:detail", args=[application.pk]) not in text, url
    listing_page = client.get(reverse("jobs:posting_detail", args=[posting.pk])).content.decode()
    assert "Discarded: The pay" not in listing_page, "the owner's reason for discarding it"


def test_the_search_finds_nothing_of_the_owner_s(client, user, world):
    client.force_login(user)
    shared = reverse("jobs:company_detail", args=[world["company"].pk])
    for secret in ("OWNER-NOTE-ON-COMPANY", "OWNER-NOTE-ON-CONTACT", "OWNER-PRIVATE-CONTACT"):
        found = client.get(reverse("core:search"), {"q": secret}).content.decode()
        # The page says what was searched for; what it must not do is find anything by it.
        assert shared not in found, f"the search for {secret} found the company"


def test_the_api_carries_nothing_of_the_owner_s(client, user, world):
    from postulo.api.models import ApiToken

    _token, raw = ApiToken.issue(user, "Leak", scopes=("read",))
    auth = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    company, posting = world["company"], world["posting"]
    for url in (
        reverse("postulo-api:get_company", args=[company.pk]),
        reverse("postulo-api:list_companies"),
        reverse("postulo-api:get_listing", args=[posting.pk]),
        reverse("postulo-api:list_listings") + "?state=all",
    ):
        answer = client.get(url, **auth)
        assert answer.status_code == 200, url
        assert_clean(answer.content.decode(), url)
    listing = json.loads(
        client.get(reverse("postulo-api:get_listing", args=[posting.pk]), **auth).content
    )
    assert listing["application_ids"] == []
    assert listing["state"] == "new" and listing["discard_reason"] == ""
    assert listing["events"] == []


def test_the_cards_and_the_files_carry_nothing_of_the_owner_s(client, user, world):
    client.force_login(user)
    company = world["company"]
    for url in (
        reverse("jobs:company_vcard", args=[company.pk]) + "?notes=1",
        reverse("jobs:company_contacts_vcard", args=[company.pk]) + "?notes=1",
        reverse("jobs:company_export", args=[company.pk]) + "?format=json&notes=1",
    ):
        answer = client.get(url)
        assert answer.status_code in (200, 404), url
        assert_clean(answer.content.decode(errors="replace"), url)


def test_the_map_draws_nothing_of_the_owner_s(user, world):
    from postulo.jobs.views import pinned_addresses

    assert_clean(json.dumps(pinned_addresses(user), default=str), "the map")


def test_the_whole_export_carries_nothing_of_the_owner_s(user, world):
    from postulo.core import export

    assert_clean(json.dumps(export.build_document(user), default=str), "the export")


def test_the_company_table_counts_only_what_the_reader_can_see(client, user, world):
    rows = Company.objects.visible_to(user).with_table_data(user).get(pk=world["company"].pk)
    assert rows.contact_count == 0 and rows.application_count == 0
    assert rows.posting_count == 1


def test_the_archive_carries_only_the_exporter_s_own_appointments(user, world):
    """The grantee has a meeting of their own at the shared company, which makes the archive
    carry the company; the owner's meetings there are not theirs to take away (#966)."""
    from postulo.core import export

    Appointment.objects.create(
        owner=user, company=world["company"], starts_at=timezone.now(), notes="MY-OWN-MEETING"
    )
    document = export.build_document(user)
    text = json.dumps(document, default=str)
    assert "MY-OWN-MEETING" in text
    assert_clean(text, "the archive")
    written = sum(len(company["appointments"]) for company in document["companies"])
    assert written == export.counts(user)["appointments"] == 1


def a_team(*members):
    team = Group.objects.create(name="Team")
    team.user_set.add(*members)
    return team


def owned_by(team, model, **fields):
    record = model(**fields)
    record.set_principal(team)
    record.save()
    return record


def test_two_members_of_a_group_each_export_only_their_own_meetings(user, other_user):
    from postulo.core import export
    from postulo.jobs.models import CompanyKind

    team = a_team(user, other_user)
    service = owned_by(team, Company, name="Jobcentre", kind=CompanyKind.EMPLOYMENT_SERVICE)
    for member, note in ((user, "FIRST-MEMBER-MEETING"), (other_user, "SECOND-MEMBER-MEETING")):
        Appointment.objects.create(
            owner=member, company=service, starts_at=timezone.now(), notes=note
        )
    for member, mine, theirs in (
        (user, "FIRST-MEMBER-MEETING", "SECOND-MEMBER-MEETING"),
        (other_user, "SECOND-MEMBER-MEETING", "FIRST-MEMBER-MEETING"),
    ):
        document = export.build_document(member)
        text = json.dumps(document, default=str)
        assert mine in text and theirs not in text
        written = sum(len(company["appointments"]) for company in document["companies"])
        assert written == export.counts(member)["appointments"] == 1


def test_the_merge_plan_names_only_the_viewer_s_meetings(user, other_user):
    from postulo.jobs import merging
    from postulo.jobs.models import CompanyKind

    team = a_team(user, other_user)
    kept = owned_by(team, Company, name="A", kind=CompanyKind.EMPLOYMENT_SERVICE)
    other = owned_by(team, Company, name="B", kind=CompanyKind.EMPLOYMENT_SERVICE)
    when = timezone.now()
    Appointment.objects.create(owner=user, company=other, starts_at=when)
    Appointment.objects.create(owner=other_user, company=other, starts_at=when)
    plan = merging.plan_companies(kept, other, user)
    line = next(line for line in plan.moves if line.label == "Appointments")
    assert line.count == 1


def delete_dialog_world(owner, grantee, scope):
    company = Company.objects.create(owner=owner, name="Dialog Co")
    posting = create_listing(owner, company=company, posting_data={"title": "Dialog role"})
    Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)
    Appointment.objects.create(owner=owner, company=company, starts_at=timezone.now())
    record_listing_event(posting, summary="OWNER-HISTORY-ENTRY", owner=owner)
    Share.objects.create(company=company, grantee_user=grantee, scope=scope)
    Share.objects.create(posting=posting, grantee_user=grantee, scope=scope)
    return company, posting


def test_a_reader_gets_no_delete_dialog_and_no_counts_behind_it(client, user, other_user):
    company, posting = delete_dialog_world(other_user, user, Share.Scope.READ)
    client.force_login(user)
    for url, dialog in (
        (reverse("jobs:company_detail", args=[company.pk]), "delete-company"),
        (reverse("jobs:posting_detail", args=[posting.pk]), "delete-posting"),
    ):
        page = client.get(url).content.decode()
        assert dialog not in page, url
        assert "1 application" not in page and "1 appointment" not in page, url
    # The owner is offered it, and sees the counts of their own rows.
    client.force_login(other_user)
    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "delete-company" in page and "1 appointment" in page


def test_the_delete_dialog_counts_only_the_manager_s_own_rows(client, user, other_user):
    """A group's company: the other member's applications, meetings and history are not
    counted for this one (#958)."""
    from postulo.core import deletions
    from postulo.jobs.views import CompanyDeleteView, PostingDeleteView

    team = a_team(user, other_user)
    company = owned_by(team, Company, name="Team Co")
    posting = owned_by(team, JobPosting, company=company, title="Team role")
    Application.objects.create(owner=other_user, posting=posting, status=Status.APPLIED)
    Appointment.objects.create(owner=other_user, company=company, starts_at=timezone.now())
    record_listing_event(posting, summary="THEIR-HISTORY", owner=other_user)
    client.force_login(user)
    request = client.get(reverse("jobs:company_detail", args=[company.pk])).wsgi_request
    for view, record in ((CompanyDeleteView, company), (PostingDeleteView, posting)):
        facts = json.dumps(deletions.facts_for(view, request, record), default=str)
        assert "application" not in facts and "appointment" not in facts, facts
        assert "history" not in facts and "documents" not in facts, facts


def make_user(name):
    return get_user_model().objects.create_user(
        email=f"{name}@example.org", username=name, password="not-a-real-pw"
    )


def test_a_private_child_of_a_shared_company_is_not_named_on_its_page(client, user, other_user):
    """Case 1 of #957: the grantee ties their private company under the owner's shared one.
    The link is refused when saved, and were it in the database the page would not name it."""
    shared = Company.objects.create(owner=other_user, name="Shared Parent")
    Share.objects.create(company=shared, grantee_user=user, scope=Share.Scope.READ)
    third = make_user("third")
    Share.objects.create(company=shared, grantee_user=third, scope=Share.Scope.READ)

    client.force_login(user)
    refused = client.post(
        reverse("jobs:company_create"),
        {"name": "PRIVATE-CHILD-NAME", "kind": "employer", "parent": shared.pk},
    )
    assert refused.status_code == 200 and "would be shown to" in refused.content.decode()
    assert not Company.objects.filter(name="PRIVATE-CHILD-NAME").exists()

    Company.objects.create(
        owner=user, name="PRIVATE-CHILD-NAME", parent=shared, location="PRIVATE-TOWN"
    )
    for viewer in (other_user, third):
        client.force_login(viewer)
        page = client.get(reverse("jobs:company_detail", args=[shared.pk])).content.decode()
        assert "PRIVATE-CHILD-NAME" not in page and "PRIVATE-TOWN" not in page
        assert "across=group" not in page


def test_a_parent_that_is_no_longer_shared_is_not_named(client, user, other_user):
    """Case 2 of #957: the child was shared with the reader, the parent never was."""
    parent = Company.objects.create(owner=other_user, name="WITHDRAWN-PARENT")
    child = Company.objects.create(owner=other_user, name="Child Co", parent=parent)
    Share.objects.create(company=child, grantee_user=user, scope=Share.Scope.READ)
    client.force_login(user)
    for url in (
        reverse("jobs:company_detail", args=[child.pk]),
        reverse("jobs:company_list"),
    ):
        assert "WITHDRAWN-PARENT" not in client.get(url).content.decode(), url
