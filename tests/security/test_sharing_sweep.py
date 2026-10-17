"""What a grant reaches, through every address that names a record (#896).

The isolation sweep says that another account's record is a 404. A share changes that for
three kinds of record -- a company, a contact, a listing -- and for nothing else, and this
sweep says by how much: every company, contact and listing the other account has is shared
with this one, and then every address is tried.

* **read** addresses answer a read-only grant: the record's page, its cards and files, and
  what a person keeps of their own on it -- their decision about a listing, their history of
  it, their application, the contacts they add at a company;
* **write** addresses change the record's details, and need a read/write grant;
* **manage** addresses delete it, merge it or give it away, and no grant reaches them: they
  are its owner's, or every member's of the group that owns it;
* and everything else -- applications, captures, documents, industries -- stays a 404,
  because sharing a listing never shares anybody's application to it.
"""

import json

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, Contact, JobPosting, Share

from .test_isolation_sweep import API, FACTORIES, route_of

pytestmark = pytest.mark.django_db

READ = {
    "jobs:company_detail",
    "jobs:company_logo",
    "jobs:company_vcard",
    "jobs:company_export",
    "jobs:company_contacts_vcard",
    "jobs:contact_vcard",
    "jobs:contact_export",
    "jobs:posting_detail",
    "listings:apply",
    "listings:discard",
    "listings:restore",
    "listings:shortlist",
    "listings:event_create",
    "jobs:own_note",
}
WRITE = {
    "jobs:company_cell",
    "jobs:company_identifier_remove",
    "jobs:company_wikidata",
    "jobs:company_names",
    "jobs:company_logo_action",
    "jobs:company_update",
    "jobs:posting_update",
    "listings:cell",
}
#: Seen with a read grant, saved only with a read/write one: a contact has no other page.
SEEN_SAVED_WITH_WRITE = {"jobs:contact_update"}
MANAGE = {
    "jobs:share",
    "jobs:share_revoke",
    "jobs:move_owner",
    "jobs:company_delete",
    "jobs:company_merge",
    "jobs:contact_delete",
    "jobs:contact_merge",
    "jobs:posting_delete",
}
API_READ = {
    # A person's own decision about a listing, and their own application to it.
    "postulo-api:apply",
    "postulo-api:discard",
    "postulo-api:restore",
    "postulo-api:shortlist",
    "postulo-api:get_company",
    "postulo-api:add_contact",
    "postulo-api:get_listing",
    "postulo-api:add_listing_event",
}
API_WRITE = {"postulo-api:patch_company"}


def share_everything(owner, grantee, scope):
    """Every company, contact and listing ``owner`` has, shared with ``grantee``."""
    for company in Company.objects.filter(owner=owner):
        Share.objects.get_or_create(
            company=company, grantee_user=grantee, defaults={"scope": scope}
        )
    for contact in Contact.objects.filter(owner=owner):
        Share.objects.get_or_create(
            contact=contact, grantee_user=grantee, defaults={"scope": scope}
        )
    for posting in JobPosting.objects.filter(owner=owner):
        Share.objects.get_or_create(
            posting=posting, grantee_user=grantee, defaults={"scope": scope}
        )


def test_the_lists_name_real_addresses():
    assert (READ | WRITE | SEEN_SAVED_WITH_WRITE | MANAGE) <= set(FACTORIES)
    assert (API_READ | API_WRITE) <= set(API)
    assert not (READ & WRITE) and not (READ & MANAGE) and not (WRITE & MANAGE)


def reached(response) -> bool:
    return response.status_code not in (404, 405)


@pytest.mark.parametrize("scope", [Share.Scope.READ, Share.Scope.WRITE])
@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_a_grant_reaches_its_scope_and_nothing_more(client, user, other_user, name, scope):
    url = reverse(name, kwargs=FACTORIES[name](other_user))
    share_everything(other_user, user, scope)
    client.force_login(user)

    allowed = READ | (WRITE | SEEN_SAVED_WITH_WRITE if scope == Share.Scope.WRITE else set())
    if name in SEEN_SAVED_WITH_WRITE and scope == Share.Scope.READ:
        assert client.get(url).status_code == 200, f"{name}: a read grant cannot see it"
        assert client.post(url).status_code == 404, f"{name}: a read grant can save it"
    elif name in allowed:
        answers = [client.get(url), client.post(url)]
        assert any(reached(response) for response in answers), (
            f"{name}: a {scope} grant does not reach it "
            f"({[response.status_code for response in answers]})"
        )
    else:
        for method in (client.get, client.post):
            response = method(url)
            assert response.status_code in (404, 405), (
                f"{name} {url}: {method.__name__.upper()} answered {response.status_code} "
                f"to a {scope} grant"
            )


@pytest.mark.parametrize("scope", [Share.Scope.READ, Share.Scope.WRITE])
@pytest.mark.parametrize("name", sorted(API))
def test_a_grant_reaches_its_scope_through_the_api(client, user, other_user, name, scope):
    from postulo.api.models import ApiToken

    method, factory, body = API[name]
    _record, raw = ApiToken.issue(
        user, "Sweep", scopes=("read", "write", "documents:read", "captures")
    )
    url = reverse(route_of(name), kwargs=factory(other_user))
    share_everything(other_user, user, scope)

    response = getattr(client, method)(
        url,
        data=json.dumps(body) if method != "get" else None,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    allowed = API_READ | (API_WRITE if scope == Share.Scope.WRITE else set())
    if name in allowed:
        assert response.status_code != 404, f"{name}: a {scope} grant does not reach it"
    else:
        assert response.status_code == 404, (
            f"{name} {url}: {method.upper()} answered {response.status_code} to a {scope} "
            f"grant: {response.content[:200]!r}"
        )


def test_a_revoked_grant_reaches_nothing(client, user, other_user):
    company = Company.objects.create(owner=other_user, name="Acme")
    grant = Share.objects.create(company=company, grantee_user=user, scope=Share.Scope.WRITE)
    client.force_login(user)
    url = reverse("jobs:company_detail", args=[company.pk])
    assert client.get(url).status_code == 200
    grant.delete()
    assert client.get(url).status_code == 404
