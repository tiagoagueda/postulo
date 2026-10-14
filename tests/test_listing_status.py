"""Where somebody stands with a listing is theirs (#896).

A listing can be shared; a decision about it never is. One person discarding a shared
listing discards it for nobody else, and an application on it is invisible to everyone but
its owner.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.applications.services import apply_to_listing, create_listing
from postulo.jobs.models import (
    Company,
    JobPosting,
    ListingEvent,
    ListingState,
    ListingStatus,
    NotReadFor,
    Share,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


def person(name):
    return User.objects.create_user(email=f"{name}@example.org", password="not-a-real-pw")


@pytest.fixture
def owner():
    return person("owner")


@pytest.fixture
def friend():
    return person("friend")


@pytest.fixture
def listing(owner, friend):
    company = Company.objects.create(owner=owner, name="Acme")
    Share.objects.create(company=company, grantee_user=friend, scope=Share.Scope.READ)
    posting = create_listing(owner, company=company, posting_data={"title": "Engineer"})
    Share.objects.create(posting=posting, grantee_user=friend, scope=Share.Scope.READ)
    return posting


def read(user, posting):
    return JobPosting.objects.visible_to(user).with_status(user).get(pk=posting.pk)


def test_a_listing_nobody_decided_about_reads_as_new(owner, listing):
    seen = read(owner, listing)
    assert seen.state == ListingState.NEW
    assert seen.noted_at == listing.created_at
    assert seen.decided_at is None


def test_discarding_a_shared_listing_discards_it_for_nobody_else(owner, friend, listing):
    read(friend, listing).discard(friend, "pay")
    assert read(friend, listing).state == ListingState.DISCARDED
    assert read(owner, listing).state == ListingState.NEW
    assert ListingStatus.objects.filter(posting=listing).count() == 1


def test_the_tabs_count_each_person_s_own_decisions(owner, friend, listing):
    read(owner, listing).shortlist(owner)
    mine = JobPosting.objects.visible_to(owner).with_status(owner).tab_counts()
    theirs = JobPosting.objects.visible_to(friend).with_status(friend).tab_counts()
    assert mine[ListingState.SHORTLISTED] == 1
    assert theirs[ListingState.SHORTLISTED] == 0
    assert theirs["undecided"] == 1


def test_an_application_on_a_shared_listing_is_invisible_to_everyone_but_its_owner(
    client, owner, friend, listing
):
    application = apply_to_listing(read(friend, listing), {}, owner=friend)
    assert application.owner == friend

    assert read(friend, listing).state == "new"
    assert read(friend, listing).derived_state == "applied"
    assert read(owner, listing).derived_state == "new"
    assert read(owner, listing).application_count == 0

    client.force_login(owner)
    page = client.get(reverse("jobs:posting_detail", args=[listing.pk]))
    assert page.status_code == 200
    assert reverse("applications:detail", args=[application.pk]) not in page.content.decode()


def test_the_listing_page_shows_the_reader_s_state(client, owner, friend, listing):
    read(owner, listing).discard(owner, "pay")
    client.force_login(friend)
    page = client.get(reverse("jobs:posting_detail", args=[listing.pk]))
    assert page.status_code == 200
    assert 'data-listing-state="discarded"' not in page.content.decode()


def test_the_discard_button_decides_for_whoever_presses_it(client, owner, friend, listing):
    client.force_login(friend)
    client.post(reverse("listings:discard", args=[listing.pk]), {"reason": "pay"})
    assert ListingStatus.objects.get(owner=friend, posting=listing).state == "discarded"
    assert not ListingStatus.objects.filter(owner=owner, posting=listing).exists()


def test_a_state_is_read_for_somebody_or_not_at_all(listing):
    with pytest.raises(NotReadFor):
        _ = JobPosting.objects.get(pk=listing.pk).state
    with pytest.raises(NotReadFor):
        JobPosting.objects.all().undecided()


def test_each_person_keeps_their_own_history_of_a_shared_listing(owner, friend, listing):
    from postulo.jobs.history import history_of, record_listing_event

    record_listing_event(listing, summary="The owner's call", owner=owner)
    record_listing_event(listing, summary="The friend's call", owner=friend)
    assert [e.summary for e in history_of(listing, owner)] == ["The owner's call"]
    assert [e.summary for e in history_of(listing, friend)] == ["The friend's call"]
    assert set(ListingEvent.objects.for_user(friend).values_list("summary", flat=True)) == {
        "The friend's call"
    }


def test_nobody_writes_into_a_history_of_a_listing_they_cannot_see(owner, listing):
    from postulo.jobs.history import NotBound, record_listing_event

    stranger = person("stranger")
    with pytest.raises(NotBound):
        record_listing_event(listing, summary="Not mine", owner=stranger)
