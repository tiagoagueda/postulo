"""A note on a company or a contact is one person's (#896).

Companies and contacts can be shared, and a note never is: two people on one shared company
each see their own, and nothing either writes reaches the other.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.jobs.models import Company, CompanyNote, Contact, ContactNote, Share

User = get_user_model()

pytestmark = pytest.mark.django_db

PASSWORD = "not-a-real-password"


def person(name):
    return User.objects.create_user(email=f"{name}@example.org", password=PASSWORD)


@pytest.fixture
def owner():
    return person("owner")


@pytest.fixture
def friend():
    return person("friend")


@pytest.fixture
def shared(owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    Share.objects.create(company=company, grantee_user=friend, scope=Share.Scope.WRITE)
    return company


def signed_in(client, who):
    client.force_login(who)
    return client


def test_two_people_on_one_shared_company_each_see_only_their_own_note(
    client, owner, friend, shared
):
    shared.write_note(owner, "The owner's private thought.")
    shared.write_note(friend, "The friend's private thought.")

    page = signed_in(client, friend).get(reverse("jobs:company_detail", args=[shared.pk]))
    assert page.status_code == 200
    assert "The friend&#x27;s private thought." in page.content.decode()
    assert "owner&#x27;s private thought" not in page.content.decode()

    page = signed_in(client, owner).get(reverse("jobs:company_detail", args=[shared.pk]))
    assert "The owner&#x27;s private thought." in page.content.decode()
    assert "friend&#x27;s private thought" not in page.content.decode()


def test_saving_the_company_form_writes_the_saver_s_note_and_no_one_else_s(
    client, owner, friend, shared
):
    shared.write_note(owner, "Kept by the owner.")
    response = signed_in(client, friend).post(
        reverse("jobs:company_update", args=[shared.pk]),
        {"name": "Acme", "kind": "employer", "notes": "Written by the friend."},
    )
    assert response.status_code in (200, 302)
    assert CompanyNote.objects.get(owner=friend, company=shared).text == "Written by the friend."
    assert CompanyNote.objects.get(owner=owner, company=shared).text == "Kept by the owner."


def test_a_blank_note_is_no_note(owner):
    company = Company.objects.create(owner=owner, name="Acme")
    company.write_note(owner, "Something")
    company.write_note(owner, "   ")
    assert not CompanyNote.objects.filter(company=company).exists()
    assert company.note_for(owner) == ""


def test_the_search_finds_a_company_by_the_reader_s_note_only(client, owner, friend, shared):
    shared.write_note(owner, "zebracorn")
    found = signed_in(client, friend).get(reverse("core:search"), {"q": "zebracorn"})
    assert "Acme" not in found.content.decode()
    shared.write_note(friend, "zebracorn too")
    found = signed_in(client, friend).get(reverse("core:search"), {"q": "zebracorn"})
    assert "Acme" in found.content.decode()


def test_a_contact_s_note_is_its_reader_s(owner, friend):
    contact = Contact.objects.create(owner=owner, name="Jo")
    contact.write_note(owner, "Met at the fair.")
    Share.objects.create(contact=contact, grantee_user=friend, scope=Share.Scope.READ)
    seen = Contact.objects.visible_to(friend).with_notes(friend).get(pk=contact.pk)
    assert seen.notes == ""
    assert ContactNote.objects.filter(contact=contact).count() == 1


def test_a_note_is_read_for_somebody_or_not_at_all(owner):
    from postulo.jobs.models import NotReadFor

    company = Company.objects.create(owner=owner, name="Acme")
    with pytest.raises(NotReadFor):
        _ = Company.objects.get(pk=company.pk).notes


def test_a_merge_keeps_each_person_s_note_apart(owner, friend):
    from postulo.jobs.merging import merge_companies

    kept = Company.objects.create(owner=owner, name="Acme")
    other = Company.objects.create(owner=owner, name="ACME Ltd")
    kept.write_note(owner, "Mine on the first.")
    other.write_note(owner, "Mine on the second.")
    other.write_note(friend, "The friend's on the second.")
    merge_companies(kept, other)
    mine = CompanyNote.objects.get(owner=owner, company=kept).text
    assert mine.startswith("Mine on the first.") and "Mine on the second." in mine
    assert "friend" not in mine
    assert CompanyNote.objects.get(owner=friend, company=kept).text == (
        "The friend's on the second."
    )
