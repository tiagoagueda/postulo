"""Groups that own records, leaving one, and what a person keeps when they lose a record (#896)."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.applications.services import create_listing
from postulo.core.groups import GroupOwnsRecords, LastMemberOwns, delete_group, leave
from postulo.jobs import detach, ownership, sharing
from postulo.jobs.models import (
    Company,
    CompanyNote,
    Contact,
    JobPosting,
    ListingStatus,
    RecordChange,
    Share,
)
from tests.recently import signed_in_recently

User = get_user_model()

pytestmark = pytest.mark.django_db


def person(name):
    return User.objects.create_user(
        email=f"{name}@example.org", username=name, password="not-a-real-pw"
    )


@pytest.fixture
def ana():
    return person("ana")


@pytest.fixture
def ben():
    return person("ben")


@pytest.fixture
def household(ana, ben):
    group = Group.objects.create(name="Household")
    group.user_set.add(ana, ben)
    return group


def owned_by_group(group, name="Acme"):
    company = Company(name=name)
    company.set_principal(group)
    company.save()
    return company


def test_a_company_owned_by_a_group_is_seen_and_edited_by_every_member(client, ana, ben, household):
    company = owned_by_group(household)
    for member in (ana, ben):
        client.force_login(member)
        assert client.get(reverse("jobs:company_detail", args=[company.pk])).status_code == 200
        response = client.post(
            reverse("jobs:company_update", args=[company.pk]),
            {"name": "Acme", "kind": "employer", "website": f"https://{member.username}.example"},
        )
        assert response.status_code == 302
    company.refresh_from_db()
    assert company.website == "https://ben.example"


def test_every_member_may_delete_or_share_a_group_s_record(ana, ben, household):
    company = owned_by_group(household)
    assert Company.objects.manageable_by(ben).filter(pk=company.pk).exists()
    outsider = person("outsider")
    sharing.share(company, outsider, "read", ben)
    assert Company.objects.visible_to(outsider).filter(pk=company.pk).exists()


def test_leaving_copies_what_your_own_data_points_at(ana, ben, household):
    company = owned_by_group(household)
    posting = JobPosting(company=company, title="Engineer")
    posting.set_principal(household)
    posting.save()
    application = Application.objects.create(owner=ben, posting=posting, status=Status.APPLIED)
    company.write_note(ben, "Ben's own note")
    posting.discard(ben, "pay")

    leave(household, ben)

    application.refresh_from_db()
    copy = application.posting
    assert copy.pk != posting.pk and copy.owner == ben and copy.owner_group is None
    assert copy.company.pk != company.pk and copy.company.owner == ben
    assert CompanyNote.objects.get(owner=ben).company == copy.company
    assert ListingStatus.objects.get(owner=ben).posting == copy
    assert RecordChange.objects.filter(object_id=copy.pk, action="copied").exists()
    # The group keeps its records, and Ben no longer sees them.
    assert Company.objects.filter(pk=company.pk, owner_group=household).exists()
    assert not Company.objects.visible_to(ben).filter(pk=company.pk).exists()
    assert Company.objects.visible_to(ana).filter(pk=company.pk).exists()


def test_leaving_copies_nothing_your_data_does_not_point_at(ana, ben, household):
    owned_by_group(household, "Untouched")
    leave(household, ben)
    assert not Company.objects.filter(owner=ben).exists()


def test_an_administrator_removing_someone_does_the_same(client, ana, ben, household):
    company = owned_by_group(household)
    company.write_note(ben, "kept")
    root = User.objects.create_superuser(email="root@example.org", password="not-a-real-pw")
    signed_in_recently(client, root)
    client.post(
        reverse("server:groups"),
        {"action": "save", "group": household.pk, "name": "Household", "members": [ana.pk]},
    )
    household.refresh_from_db()
    assert list(household.user_set.all()) == [ana]
    assert CompanyNote.objects.get(owner=ben).company.owner == ben


def test_the_last_member_cannot_leave_a_group_that_owns_records(ana, ben, household):
    owned_by_group(household)
    leave(household, ben)
    with pytest.raises(LastMemberOwns):
        leave(household, ana)
    assert household.user_set.filter(pk=ana.pk).exists()


def test_a_group_that_owns_records_cannot_be_deleted(household):
    owned_by_group(household)
    with pytest.raises(GroupOwnsRecords):
        delete_group(household)


def test_a_share_the_leaver_gave_the_group_ends_with_their_membership(ana, ben, household):
    mine = Company.objects.create(owner=ben, name="Ben's")
    sharing.share(mine, household, "read", ben)
    contact = Contact.objects.create(owner=ana, company=mine, name="Ana's contact at Ben's")
    leave(household, ben)
    assert not Share.objects.filter(company=mine).exists()
    contact.refresh_from_db()
    assert contact.company.owner == ana, "Ana's contact keeps a company of her own"


def test_revoking_a_share_copies_what_the_grantee_s_data_points_at(ana, ben):
    company = Company.objects.create(owner=ana, name="Acme")
    posting = create_listing(ana, company=company, posting_data={"title": "Engineer"})
    sharing.share(posting, ben, "read", ana, also_share=True)
    application = Application.objects.create(owner=ben, posting=posting, status=Status.APPLIED)
    sharing.revoke(Share.objects.get(posting=posting), ana)
    application.refresh_from_db()
    assert application.posting.owner == ben and application.posting.pk != posting.pk


def test_deleting_a_shared_listing_keeps_the_other_side_s_application(client, ana, ben):
    company = Company.objects.create(owner=ana, name="Acme")
    posting = create_listing(ana, company=company, posting_data={"title": "Engineer"})
    sharing.share(posting, ben, "read", ana, also_share=True)
    application = Application.objects.create(owner=ben, posting=posting, status=Status.APPLIED)
    client.force_login(ana)
    client.post(reverse("jobs:posting_delete", args=[posting.pk]))
    assert not JobPosting.objects.filter(pk=posting.pk).exists()
    application.refresh_from_db()
    assert application.posting.owner == ben


def test_giving_a_record_to_a_group_and_back(ana, ben, household):
    company = Company.objects.create(owner=ana, name="Acme")
    company.phone_numbers.create(number="+351211234567", owner=ana)
    ownership.move(company, household, ana)
    company.refresh_from_db()
    assert company.owner_group == household and company.owner is None
    assert company.phone_numbers.get().owner_group == household
    assert Company.objects.manageable_by(ben).filter(pk=company.pk).exists()
    ownership.move(company, ben, ben)
    company.refresh_from_db()
    assert company.owner == ben
    assert not Company.objects.visible_to(ana).filter(pk=company.pk).exists()


def test_every_relation_to_the_three_is_accounted_for():
    """A new foreign key to a company, a contact or a listing must say what leaving does."""
    from django.apps import apps

    known = {(app, name, field) for kind in detach.DEPENDENTS.values() for app, name, field in kind}
    known |= {
        (app, name, field) for kind in detach.DEPENDENT_LINKS.values() for app, name, field in kind
    }
    known |= detach.NOT_DEPENDENTS
    for label in ("jobs.Company", "jobs.Contact", "jobs.JobPosting"):
        for relation in apps.get_model(label)._meta.related_objects:
            key = (
                relation.related_model._meta.app_label,
                relation.related_model.__name__,
                relation.field.name,
            )
            if relation.related_model.__name__ in ("CompanyNote", "ContactNote", "ListingStatus"):
                assert key in known, key
                continue
            assert key in known, f"{key}: add it to jobs.detach.DEPENDENTS or NOT_DEPENDENTS"


def test_a_person_leaves_a_group_from_their_own_page(client, ana, ben, household):
    company = owned_by_group(household)
    company.write_note(ben, "mine")
    client.force_login(ben)
    page = client.get(reverse("accounts:groups")).content.decode()
    assert "Household" in page
    client.post(reverse("accounts:group_leave", args=[household.pk]))
    assert household.user_set.filter(pk=ben.pk).exists(), "nothing happens without saying so"
    client.post(reverse("accounts:group_leave", args=[household.pk]), {"confirmed": "1"})
    assert not household.user_set.filter(pk=ben.pk).exists()
    assert CompanyNote.objects.get(owner=ben).company.owner == ben


def test_a_list_shows_whose_was_asked_for_and_everybody_s_only_to_a_superuser(
    client, ana, ben, household
):
    owned_by_group(household, "Ours")
    Company.objects.create(owner=ana, name="Ana's")
    Company.objects.create(owner=person("carla"), name="Carla's")
    client.force_login(ana)
    mine = client.get(reverse("jobs:company_list"), {"whose": "mine"}).content.decode()
    assert "Ana&#x27;s" in mine and "Ours" not in mine
    ours = client.get(reverse("jobs:company_list"), {"whose": "groups"}).content.decode()
    assert "Ours" in ours and "Ana&#x27;s" not in ours
    everybody = client.get(reverse("jobs:company_list"), {"whose": "everyone"}).content.decode()
    assert "Carla&#x27;s" not in everybody, "only an administrator may ask for everybody's"
    root = User.objects.create_superuser(email="root@example.org", password="not-a-real-pw")
    client.force_login(root)
    everybody = client.get(reverse("jobs:company_list"), {"whose": "everyone"}).content.decode()
    assert "Carla&#x27;s" in everybody


def test_a_file_imported_into_a_group_makes_the_group_s_records(client, ana, household):
    from postulo.core import kind_files

    held = {
        "kind": "companies",
        "rows": [{"name": "Imported Co", "kind": "employer"}],
        "into": f"group:{household.pk}",
    }
    kind_files.apply(ana, held)
    made = Company.objects.get(name="Imported Co")
    assert made.owner_group == household and made.owner is None


def test_an_import_cannot_name_a_group_one_is_not_in(ana):
    from postulo.core import kind_files

    elsewhere = Group.objects.create(name="Elsewhere")
    held = {"kind": "companies", "rows": [{"name": "Mine Co"}], "into": f"group:{elsewhere.pk}"}
    kind_files.apply(ana, held)
    assert Company.objects.get(name="Mine Co").owner == ana


def test_an_export_carries_the_group_s_records_and_what_one_used_of_a_shared_one(
    ana, ben, household
):
    from postulo.core import export

    owned_by_group(household, "Ours")
    theirs = Company.objects.create(owner=person("carla"), name="Theirs")
    Share.objects.create(company=theirs, grantee_user=ana, scope="read")
    unused = Company.objects.create(owner=person("dora"), name="Unused")
    Share.objects.create(company=unused, grantee_user=ana, scope="read")
    theirs.write_note(ana, "Ana's note on it")

    names = {row["name"]: row for row in export.build_document(ana)["companies"]}
    assert "Ours" in names and "shared_from" not in names["Ours"]
    assert names["Theirs"]["shared_from"] == "carla"
    assert names["Theirs"]["notes"] == "Ana's note on it"
    assert "Unused" not in names


def test_the_same_company_held_by_a_person_and_their_group_is_a_conflict(
    client, ana, ben, household
):
    from postulo.jobs import conflicts

    mine = Company.objects.create(owner=ana, name="Acme")
    ours = owned_by_group(household, "Acme")
    found = conflicts.find(ana)
    assert [(p.first.pk, p.second.pk) for p in found.pairs] == [(mine.pk, ours.pk)]
    assert not conflicts.find(person("stranger")).pairs

    client.force_login(ana)
    assert "Acme" in client.get(reverse("jobs:conflicts")).content.decode()
    client.post(reverse("jobs:conflicts"), {"kind": "company", "first": mine.pk, "second": ours.pk})
    assert not conflicts.find(ana).pairs


def test_a_merge_across_owners_never_takes_a_record_from_somebody(ana, ben, household):
    from postulo.jobs import merging

    mine = Company.objects.create(owner=ana, name="Acme")
    ours = owned_by_group(household, "ACME")
    bens = JobPosting(company=ours, title="Ben's own listing", owner=ben)
    bens.save()
    with pytest.raises(merging.CannotMerge, match="ben"):
        merging.merge_companies(mine, ours)
    mine.write_note(ana, "kept")
    merging.merge_companies(ours, mine)
    assert not Company.objects.filter(pk=mine.pk).exists()
    bens.refresh_from_db()
    assert bens.company == ours, "a member's own listing at the group's company stays at it"
    assert CompanyNote.objects.get(owner=ana).company == ours


def _root(client):
    root = User.objects.create_superuser(email="root@example.org", password="not-a-real-pw")
    signed_in_recently(client, root)
    return root


def test_a_membership_change_is_logged_with_who_came_and_who_went(
    client, caplog, ana, ben, household
):
    """Membership authorises a group's records, so the log names the change (#945)."""
    cleo = person("cleo")
    _root(client)
    with caplog.at_level("WARNING", logger="postulo.core.views_access"):
        client.post(
            reverse("server:groups"),
            {
                "action": "save",
                "group": household.pk,
                "name": "Household",
                "members": [ana.pk, cleo.pk],
            },
        )
    line = next(r.getMessage() for r in caplog.records if "set by root" in r.getMessage())
    assert "added ['cleo']" in line and "removed ['ben']" in line
    assert "@example.org" not in line, "usernames, never addresses"


def test_changing_groups_asks_for_a_recent_sign_in(client, ana, ben, household):
    root = User.objects.create_superuser(email="root@example.org", password="not-a-real-pw")
    client.force_login(root)
    response = client.post(
        reverse("server:groups"),
        {"action": "save", "group": household.pk, "name": "Household", "members": [root.pk]},
    )
    assert response.status_code == 302 and "reauthenticate" in response["Location"]
    assert not household.user_set.filter(pk=root.pk).exists()


def test_your_groups_lists_who_is_in_each_one(client, ana, ben, household):
    client.force_login(ana)
    page = client.get(reverse("accounts:groups")).content.decode()
    assert "<bdi>ben</bdi>" in page and "<bdi>ana</bdi>" in page


def test_a_deactivated_member_stays_and_is_marked_but_none_is_added(client, ana, ben, household):
    ben.is_active = False
    ben.save()
    cleo = person("cleo")
    cleo.is_active = False
    cleo.save()
    _root(client)
    url = reverse("server:groups")
    assert "(deactivated)" in client.get(url).content.decode()
    client.post(
        url,
        {
            "action": "save",
            "group": household.pk,
            "name": "Household",
            "members": [ana.pk, ben.pk, cleo.pk],
        },
    )
    assert set(household.user_set.values_list("username", flat=True)) == {"ana", "ben"}
    client.force_login(ana)
    assert "deactivated" in client.get(reverse("accounts:groups")).content.decode()


def test_a_group_that_is_not_a_number_is_a_404_and_not_a_server_error(client):
    _root(client)
    response = client.post(reverse("server:groups"), {"action": "delete", "group": "abc"})
    assert response.status_code == 404


def test_a_name_taken_between_the_look_and_the_make_is_refused_not_a_500(client, monkeypatch):
    """The unique name decides; the earlier look can lose the race (#948)."""
    Group.objects.create(name="Team")
    _root(client)
    from postulo.core import views_access

    class Blind:
        def exists(self):
            return False

    real = Group.objects.filter
    monkeypatch.setattr(
        Group.objects,
        "filter",
        lambda *a, **k: Blind() if "name__iexact" in k else real(*a, **k),
    )
    # The page's own check (case-insensitive) is blind; the exact-name insert still collides.
    assert views_access._make_group("Team") is False
    monkeypatch.undo()
    assert Group.objects.filter(name="Team").count() == 1


def test_removing_a_member_locks_the_group_row_first(ana, ben, household, monkeypatch):
    """The last-member check runs under a lock on PostgreSQL (#989)."""
    from django.db import transaction
    from django.db.models.query import QuerySet

    seen = []
    real = QuerySet.select_for_update

    def watch(self, *a, **k):
        seen.append(self.model)
        return real(self, *a, **k)

    monkeypatch.setattr(QuerySet, "select_for_update", watch)
    with transaction.atomic():
        household.user_set.remove(ben)
    assert Group in seen
