"""Nothing private comes along with a shared record (#896).

The owner shares a company and a listing. Around them they keep things of their own -- a
note, a decision, an application, a private contact at the same company, a history entry --
and none of it may reach the person the records were shared with, through any surface:
the pages, the API, the search, the map, the vCard, the export.
"""

import json

import pytest
from django.urls import reverse

from postulo.applications.models import Application, EventKind, Status
from postulo.applications.services import create_listing, record_event
from postulo.jobs.history import record_listing_event
from postulo.jobs.models import Company, Contact, Share

pytestmark = pytest.mark.django_db

SECRETS = (
    "OWNER-NOTE-ON-COMPANY",
    "OWNER-PRIVATE-CONTACT",
    "OWNER-NOTE-ON-CONTACT",
    "OWNER-HISTORY-ENTRY",
    "OWNER-APPLICATION-NOTE",
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
