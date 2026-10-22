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
    "jobs:company_remove_row",
    "jobs:contact_remove_row",
    "jobs:company_wikidata",
    "jobs:company_names",
    "jobs:company_signal_add",
    "jobs:company_signal_edit",
    "jobs:company_signal_remove",
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
    "postulo-api:get_listing",
    "postulo-api:add_listing_event",
}
#: Adding a contact to a company changes the company's people, so it needs the write grant (#328).
API_WRITE = {
    "postulo-api:patch_company",
    "postulo-api:add_signal",
    "postulo-api:remove_signal",
    "postulo-api:add_contact",
}


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


# ------------------------------------------------- deleting a shared contact (#956, #961)


def shared_referee(user, other_user, scope=Share.Scope.READ):
    """``user``'s contact, shared with ``other_user``, and a reference letter from them held
    by each of the two, plus ``other_user``'s own career referee entry and application."""
    from django.core.files.base import ContentFile

    from postulo.applications.models import Application
    from postulo.documents.models import ReferenceLetter, UploadedDocument
    from postulo.resume.models import Reference

    contact = Contact.objects.create(owner=user, name="Ada Referee")
    Share.objects.create(contact=contact, grantee_user=other_user, scope=scope)
    uploads = {}
    for holder in (user, other_user):
        upload = UploadedDocument.objects.create(
            owner=holder,
            title=f"Letter of {holder.pk}",
            kind="reference",
            file=ContentFile(b"%PDF-1.7 ref\n%%EOF", name="r.pdf"),
        )
        ReferenceLetter.objects.create(owner=holder, upload=upload, referee=contact)
        uploads[holder.pk] = upload
    company = Company.objects.create(owner=other_user, name="Their Employer")
    posting = JobPosting.objects.create(owner=other_user, company=company, title="Engineer")
    application = Application.objects.create(owner=other_user, posting=posting, contact=contact)
    # The model refuses a referee that is not the owner's own, so the row a grantee is left
    # holding (a contact that was theirs, then moved or shared on) is made past that check.
    stub = Contact.objects.create(owner=other_user, name="Stub")
    reference = Reference.objects.create(owner=other_user, contact=stub)
    Reference.objects.filter(pk=reference.pk).update(contact=contact)
    reference.refresh_from_db()
    return contact, uploads, application, reference


@pytest.mark.parametrize("scope", [Share.Scope.READ, Share.Scope.WRITE])
@pytest.mark.parametrize("offered", [True, False])
def test_deleting_a_shared_contact_deletes_only_the_deleters_letters(
    client, user, other_user, scope, offered
):
    from django.contrib.messages import get_messages

    from postulo.documents.models import UploadedDocument
    from postulo.plugins.gdpr import GDPR
    from postulo.plugins.models import PluginPolicy

    if not offered:
        PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)
    contact, uploads, _application, _reference = shared_referee(user, other_user, scope)
    client.force_login(user)
    url = reverse("jobs:contact_delete", args=[contact.pk])

    # The dialog counts the deleter's letter, not the grantee's.
    assert "1 reference letter they wrote is kept" in client.get(url).content.decode()

    response = client.post(url, {"delete_letters": "1"})

    assert response.status_code == 302
    assert not Contact.objects.filter(pk=contact.pk).exists()
    assert not UploadedDocument.objects.filter(pk=uploads[user.pk].pk).exists()
    assert UploadedDocument.objects.filter(pk=uploads[other_user.pk].pk).exists(), (
        "another member's letter went with the contact"
    )
    if offered:
        said = " ".join(str(m) for m in get_messages(response.wsgi_request))
        assert "application" not in said, "the summary counted the grantee's application"
        assert "letter" not in said, "the summary counted the grantee's letter"


@pytest.mark.parametrize("offered", [True, False])
def test_deleting_a_shared_contact_leaves_the_others_a_copy(client, user, other_user, offered):
    from postulo.documents.models import ReferenceLetter
    from postulo.plugins.gdpr import GDPR
    from postulo.plugins.models import PluginPolicy

    if not offered:
        PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)
    contact, uploads, application, reference = shared_referee(user, other_user)
    client.force_login(user)

    client.post(reverse("jobs:contact_delete", args=[contact.pk]))

    assert not Contact.objects.filter(pk=contact.pk).exists()
    reference.refresh_from_db()
    application.refresh_from_db()
    copy = reference.contact
    assert copy is not None and copy.owner_id == other_user.pk
    assert application.contact_id == copy.pk
    assert ReferenceLetter.objects.get(upload=uploads[other_user.pk]).referee_id == copy.pk
