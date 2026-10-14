"""Who sees, changes and manages a company, a contact or a listing (#896).

The truth table of `visible_to`, `visible_to(write=True)` and `manageable_by`, for every way
a person can reach a record: owning it, a group of theirs owning it, a share to them or to a
group of theirs, read-only or read/write -- and for every way they cannot.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.db import IntegrityError, transaction

from postulo.applications.services import create_listing
from postulo.jobs.models import Company, Contact, JobPosting, Share

User = get_user_model()

pytestmark = pytest.mark.django_db


def person(name):
    return User.objects.create_user(email=f"{name}@example.org", password="not-a-real-password")


@pytest.fixture
def team():
    return Group.objects.create(name="Household")


@pytest.fixture
def owner():
    return person("owner")


@pytest.fixture
def stranger():
    return person("stranger")


def company_of(principal, name="Acme"):
    company = Company(name=name)
    company.set_principal(principal)
    company.save()
    return company


def scopes(model, user, record):
    """(sees, may change, may manage) for ``user`` on ``record``."""
    return (
        model.objects.visible_to(user).filter(pk=record.pk).exists(),
        model.objects.visible_to(user, write=True).filter(pk=record.pk).exists(),
        model.objects.manageable_by(user).filter(pk=record.pk).exists(),
    )


def test_the_owner_sees_changes_and_manages(owner):
    company = company_of(owner)
    assert scopes(Company, owner, company) == (True, True, True)


def test_a_stranger_reaches_nothing(owner, stranger):
    company = company_of(owner)
    assert scopes(Company, stranger, company) == (False, False, False)


def test_anonymous_reaches_nothing(owner):
    company = company_of(owner)
    assert not Company.objects.visible_to(AnonymousUser()).exists()
    assert not Company.objects.manageable_by(None).exists()
    assert company.pk


def test_every_member_of_the_owning_group_manages_it(owner, stranger, team):
    owner.groups.add(team)
    company = company_of(team)
    assert scopes(Company, owner, company) == (True, True, True)
    assert scopes(Company, stranger, company) == (False, False, False)


@pytest.mark.parametrize(
    ("scope", "expected"),
    [(Share.Scope.READ, (True, False, False)), (Share.Scope.WRITE, (True, True, False))],
)
def test_a_share_to_a_person_gives_its_scope_and_never_management(owner, stranger, scope, expected):
    company = company_of(owner)
    Share.objects.create(company=company, grantee_user=stranger, scope=scope, granted_by=owner)
    assert scopes(Company, stranger, company) == expected


@pytest.mark.parametrize(
    ("scope", "expected"),
    [(Share.Scope.READ, (True, False, False)), (Share.Scope.WRITE, (True, True, False))],
)
def test_a_share_to_a_group_reaches_each_member(owner, stranger, team, scope, expected):
    stranger.groups.add(team)
    contact = Contact.objects.create(owner=owner, name="Jo")
    Share.objects.create(contact=contact, grantee_group=team, scope=scope, granted_by=owner)
    assert scopes(Contact, stranger, contact) == expected


def test_a_share_of_one_record_reaches_no_other(owner, stranger):
    shared, kept = company_of(owner, "Shared"), company_of(owner, "Kept")
    Share.objects.create(company=shared, grantee_user=stranger, scope=Share.Scope.WRITE)
    assert list(Company.objects.visible_to(stranger)) == [shared]
    assert kept.pk


def test_a_listing_is_shared_like_the_others(owner, stranger):
    posting = create_listing(owner, company=company_of(owner), posting_data={"title": "Engineer"})
    Share.objects.create(posting=posting, grantee_user=stranger, scope=Share.Scope.READ)
    assert scopes(JobPosting, stranger, posting) == (True, False, False)


def test_two_shares_to_one_person_are_not_two_rows(owner, stranger, team):
    stranger.groups.add(team)
    company = company_of(owner)
    Share.objects.create(company=company, grantee_user=stranger, scope=Share.Scope.READ)
    Share.objects.create(company=company, grantee_group=team, scope=Share.Scope.WRITE)
    assert Company.objects.visible_to(stranger).count() == 1
    assert Company.objects.visible_to(stranger, write=True).count() == 1


def test_the_superuser_reaches_everything_only_when_it_is_asked_for(owner):
    root = User.objects.create_superuser(email="root@example.org", password="not-a-real-pw")
    company = company_of(owner)
    assert not Company.objects.visible_to(root).filter(pk=company.pk).exists()
    assert Company.objects.visible_to(root, everyone=True).filter(pk=company.pk).exists()
    assert Company.objects.manageable_by(root, everyone=True).filter(pk=company.pk).exists()


def test_an_ordinary_person_asking_for_everyone_gets_their_own(owner, stranger):
    company_of(owner)
    assert not Company.objects.visible_to(stranger, everyone=True).exists()


def test_owned_by_is_exactly_one_owner(owner, team):
    owner.groups.add(team)
    mine, ours = company_of(owner, "Mine"), company_of(team, "Ours")
    assert list(Company.objects.owned_by(owner)) == [mine]
    assert list(Company.objects.owned_by(team)) == [ours]


def test_a_record_has_exactly_one_owner(owner, team):
    with pytest.raises(IntegrityError), transaction.atomic():
        Company.objects.create(name="Both", owner=owner, owner_group=team)
    with pytest.raises(IntegrityError), transaction.atomic():
        Company.objects.create(name="Neither")


def test_a_person_and_a_group_may_each_have_an_acme(owner, team):
    company_of(owner, "Acme")
    company_of(team, "Acme")
    with pytest.raises(IntegrityError), transaction.atomic():
        company_of(team, "acme")


def test_a_share_names_one_record_and_one_grantee(owner, stranger, team):
    company = company_of(owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        Share.objects.create(company=company, scope=Share.Scope.READ)
    with pytest.raises(IntegrityError), transaction.atomic():
        Share.objects.create(
            company=company, grantee_user=stranger, grantee_group=team, scope=Share.Scope.READ
        )


def test_the_same_grantee_is_given_one_share_per_record(owner, stranger):
    company = company_of(owner)
    Share.objects.create(company=company, grantee_user=stranger, scope=Share.Scope.READ)
    with pytest.raises(IntegrityError), transaction.atomic():
        Share.objects.create(company=company, grantee_user=stranger, scope=Share.Scope.WRITE)


def test_a_group_that_owns_records_cannot_be_deleted(team):
    from django.db.models import ProtectedError

    company_of(team)
    with pytest.raises(ProtectedError):
        team.delete()
