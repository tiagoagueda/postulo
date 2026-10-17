"""Sharing through the pages: granting, the question about what a record points at,
taking back, a read-only contact, one's own note on it, and the record's trail (#896)."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from postulo.applications.services import create_listing
from postulo.jobs import sharing
from postulo.jobs.models import Company, Contact, ContactNote, RecordChange, Share

User = get_user_model()

pytestmark = pytest.mark.django_db


def person(name):
    return User.objects.create_user(
        email=f"{name}@example.org", username=name, password="not-a-real-pw"
    )


@pytest.fixture
def owner():
    return person("owner")


@pytest.fixture
def friend():
    return person("friend")


def test_the_owner_shares_a_company_with_a_person_named_by_username(client, owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    client.force_login(owner)
    url = reverse("jobs:share", args=["company", company.pk])
    assert client.get(url).status_code == 200
    client.post(url, {"person": "friend", "scope": "read"})
    grant = Share.objects.get(company=company)
    assert (grant.grantee_user, grant.scope, grant.granted_by) == (friend, "read", owner)

    client.force_login(friend)
    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "read-only" in page
    assert url not in page, "a grantee is not offered the sharing page"
    assert client.get(url).status_code == 404


def test_a_person_is_found_by_exactly_what_is_typed_and_never_listed(client, owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    client.force_login(owner)
    page = client.get(reverse("jobs:share", args=["company", company.pk])).content.decode()
    assert "friend" not in page, "the instance's accounts are not a directory"
    client.post(reverse("jobs:share", args=["company", company.pk]), {"person": "frie"})
    assert not Share.objects.exists()
    client.post(
        reverse("jobs:share", args=["company", company.pk]), {"person": "FRIEND@example.org"}
    )
    assert Share.objects.get().grantee_user == friend


def test_only_the_sharer_s_own_groups_are_offered(client, owner, friend):
    mine, theirs = Group.objects.create(name="Mine"), Group.objects.create(name="Theirs")
    owner.groups.add(mine)
    company = Company.objects.create(owner=owner, name="Acme")
    client.force_login(owner)
    url = reverse("jobs:share", args=["company", company.pk])
    page = client.get(url).content.decode()
    assert "Mine" in page and "Theirs" not in page
    client.post(url, {"grantee": f"group:{theirs.pk}"})
    assert not Share.objects.exists()
    client.post(url, {"grantee": f"group:{mine.pk}", "scope": "write"})
    assert Share.objects.get().grantee_group == mine


def test_sharing_a_listing_asks_about_its_company_and_shares_both_when_told(client, owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    posting = create_listing(owner, company=company, posting_data={"title": "Engineer"})
    client.force_login(owner)
    url = reverse("jobs:share", args=["listing", posting.pk])

    asked = client.post(url, {"person": "friend", "scope": "write"})
    assert asked.status_code == 200
    assert "Share what it points at too?" in asked.content.decode()
    assert not Share.objects.exists(), "nothing is shared before the question is answered"

    client.post(url, {"grantee": "friend", "scope": "write", "also": "1"})
    assert Share.objects.get(posting=posting).scope == "write"
    assert Share.objects.get(company=company).scope == "read"


def test_a_share_that_would_leave_a_hidden_reference_is_refused_whole(owner, friend):
    stranger = person("stranger")
    theirs = Company.objects.create(owner=stranger, name="Theirs")
    Share.objects.create(company=theirs, grantee_user=owner, scope="read")
    posting = create_listing(owner, company=theirs, posting_data={"title": "Engineer"})
    with pytest.raises(sharing.ShareRefused, match="not yours to share"):
        sharing.share(posting, friend, "read", owner, also_share=True)
    assert not Share.objects.filter(grantee_user=friend).exists()


def test_a_revocation_that_would_leave_a_hidden_reference_is_refused(owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    posting = create_listing(owner, company=company, posting_data={"title": "Engineer"})
    sharing.share(posting, friend, "read", owner, also_share=True)
    company_grant = Share.objects.get(company=company)
    with pytest.raises(sharing.ShareRefused):
        sharing.revoke(company_grant, owner)
    assert Share.objects.filter(company=company).exists()
    sharing.revoke(Share.objects.get(posting=posting), owner)
    sharing.revoke(Share.objects.get(company=company), owner)
    assert not Share.objects.exists()


def test_a_grantee_cannot_share_or_take_back(owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    Share.objects.create(company=company, grantee_user=friend, scope="write")
    third = person("third")
    with pytest.raises(sharing.ShareRefused):
        sharing.share(company, third, "read", friend)
    with pytest.raises(sharing.ShareRefused):
        sharing.revoke(Share.objects.get(), friend)


def test_a_contact_shared_read_only_is_seen_and_cannot_be_saved(client, owner, friend):
    contact = Contact.objects.create(owner=owner, name="Jo Bloggs", role="Recruiter")
    Share.objects.create(contact=contact, grantee_user=friend, scope="read")
    client.force_login(friend)
    url = reverse("jobs:contact_update", args=[contact.pk])

    page = client.get(url)
    assert page.status_code == 200
    html = page.content.decode()
    assert "Jo Bloggs" in html and "read-only" in html
    assert ">Save</button>" not in html

    assert client.post(url, {"name": "Changed", "role": "x"}).status_code == 404
    contact.refresh_from_db()
    assert contact.name == "Jo Bloggs"

    client.post(reverse("jobs:own_note", args=["contact", contact.pk]), {"notes": "Mine"})
    assert ContactNote.objects.get(contact=contact).owner == friend

    Share.objects.all().delete()
    assert client.get(url).status_code == 404


def test_a_shared_listing_cannot_be_moved_to_a_company_its_audience_cannot_see(
    client, owner, friend
):
    company = Company.objects.create(owner=owner, name="Acme")
    private = Company.objects.create(owner=owner, name="Private")
    posting = create_listing(owner, company=company, posting_data={"title": "Engineer"})
    sharing.share(posting, friend, "read", owner, also_share=True)
    client.force_login(owner)
    response = client.post(
        reverse("jobs:posting_update", args=[posting.pk]),
        {"company": private.pk, "title": "Engineer", "salary_period": "year"},
    )
    assert response.status_code == 200
    assert "is not shared with friend" in response.content.decode()
    posting.refresh_from_db()
    assert posting.company == company


def test_a_change_is_written_down_with_who_made_it(client, owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    Share.objects.create(company=company, grantee_user=friend, scope="write")
    client.force_login(friend)
    client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {"name": "Acme", "kind": "employer", "website": "https://acme.example"},
    )
    change = RecordChange.objects.get(object_id=company.pk, action="edited")
    assert change.actor == friend and "website" in change.fields
    client.force_login(owner)
    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "friend" in page and "website" in page


def test_a_hit_by_address_names_the_address_and_never_the_username(client, owner, friend):
    """Typing an address must not teach the username that goes with it (#943)."""
    company = Company.objects.create(owner=owner, name="Acme")
    client.force_login(owner)
    url = reverse("jobs:share", args=["company", company.pk])

    response = client.post(url, {"person": "Friend@Example.org", "scope": "read"}, follow=True)

    page = response.content.decode()
    assert "Shared with Friend@Example.org." in page
    assert "friend</bdi>" not in page and ">friend<" not in page
    assert "Friend@Example.org" in page, "the list says it as it was typed"
    assert Share.objects.get().grantee_user == friend


def test_sharing_twice_leaves_one_grant_with_the_wider_scope(owner, friend):
    """The second of two quick submits widens the first instead of colliding with it (#948)."""
    company = Company.objects.create(owner=owner, name="Acme")
    sharing.share(company, friend, "read", owner)
    sharing.share(company, friend, "write", owner)
    grant = Share.objects.get()
    assert grant.scope == "write"


def test_giving_a_listing_to_a_group_shares_a_company_and_its_parent_too(client, owner):
    """The move shares what it points at, parent included, and does not fail (#946)."""
    parent = Company.objects.create(owner=owner, name="Holding")
    company = Company.objects.create(owner=owner, name="Acme", parent=parent)
    posting = create_listing(owner, company=company, posting_data={"title": "Engineer"})
    team = Group.objects.create(name="Team")
    other = person("other")
    team.user_set.add(owner, other)
    client.force_login(owner)

    response = client.post(
        reverse("jobs:move_owner", args=["listing", posting.pk]),
        {"to": f"group:{team.pk}", "confirmed": "1"},
    )

    assert response.status_code == 302
    posting.refresh_from_db()
    assert posting.owner_group_id == team.pk
    assert {(s.company_id, s.scope) for s in Share.objects.filter(grantee_group=team)} == {
        (company.pk, "read"),
        (parent.pk, "read"),
    }


def test_a_refused_move_is_a_message_and_not_a_server_error(client, owner):
    company = Company.objects.create(owner=owner, name="Acme")
    team = Group.objects.create(name="Team")
    team.user_set.add(owner)
    client.force_login(owner)
    from unittest import mock

    with mock.patch(
        "postulo.jobs.ownership.move", side_effect=sharing.ShareRefused("not like that")
    ):
        response = client.post(
            reverse("jobs:move_owner", args=["company", company.pk]),
            {"to": f"group:{team.pk}", "confirmed": "1"},
        )
    assert response.status_code == 302


def test_a_superuser_cannot_give_a_record_to_a_deactivated_account(client, owner):
    """Sharing refuses a deactivated account, and so does giving (#947)."""
    gone = person("gone")
    gone.is_active = False
    gone.save()
    boss = User.objects.create_superuser(email="boss@example.org", password="not-a-real-pw")
    company = Company.objects.create(owner=owner, name="Acme")
    client.force_login(boss)

    client.post(
        reverse("jobs:move_owner", args=["company", company.pk]),
        {"person": "gone", "confirmed": "1"},
    )

    company.refresh_from_db()
    assert company.owner_id == owner.pk
