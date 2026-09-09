"""A list of ids from the client, and the rule that makes acting on it safe (#134).

Every view until now fetched *one* object through `OwnedObjectMixin`, where a foreign id is a
404. A bulk action receives a list of ids from a browser, which is exactly the shape of request
that goes wrong, so the rule is one function and this file is what holds it to it.

**Every id is re-scoped, and the action works on the intersection.** Not on the list that was
sent, and not by refusing the whole batch when one id is foreign — refusing is an answer, and
an answer tells the sender which ids exist. Forty ids of which thirty-nine belong to somebody
else changes the one, says so, and reveals nothing about the rest.

This is the security file for the feature, not a supplement to it: the counting, the ordering
and the buttons are tested beside the tables, and what is here is the boundary.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.core import bulk
from postulo.core.models import Tag
from postulo.jobs.models import Company, Industry

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-enough-password-42"


@pytest.fixture
def stranger(django_user_model):
    return django_user_model.objects.create_user(
        email="stranger@example.org", username="stranger", password=PASSWORD
    )


def an_application(owner, title="Engineer") -> Application:
    from postulo.jobs.models import Company as CompanyModel
    from postulo.jobs.models import JobPosting

    company = CompanyModel.objects.create(owner=owner, name=f"{title} Ltd")
    posting = JobPosting.objects.create(owner=owner, company=company, title=title)
    return Application.objects.create(owner=owner, posting=posting, status=Status.DRAFT)


# ------------------------------------------------------ somebody else's rows


def test_a_foreign_id_changes_nothing(client, user, stranger):
    mine = an_application(user)
    theirs = an_application(stranger, "Theirs")
    tag = Tag.objects.create(owner=user, name="shortlist")
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk, theirs.pk], "bulk-action": "tag", "tag": tag.pk},
    )

    assert mine.tags.count() == 1
    assert theirs.tags.count() == 0, "somebody else's application was not touched"


def test_the_batch_is_not_refused_because_one_id_is_foreign(client, user, stranger):
    """Refusing is an answer, and an answer says which ids exist."""
    mine = an_application(user)
    theirs = an_application(stranger, "Theirs")
    tag = Tag.objects.create(owner=user, name="shortlist")
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk, theirs.pk], "bulk-action": "tag", "tag": tag.pk},
        follow=True,
    )

    assert mine.tags.count() == 1, "the one that was theirs to change, changed"


def test_what_is_said_afterwards_counts_only_what_changed(client, user, stranger):
    """The difference between sent and changed is a count of somebody else's rows."""
    mine = an_application(user)
    theirs = [an_application(stranger, f"Theirs {n}") for n in range(4)]
    tag = Tag.objects.create(owner=user, name="shortlist")
    client.force_login(user)

    response = client.post(
        reverse("applications:bulk"),
        {
            "chosen": [mine.pk, *[row.pk for row in theirs]],
            "bulk-action": "tag",
            "tag": tag.pk,
        },
        follow=True,
    )

    said = response.content.decode()
    assert "1 application changed" in said
    assert "5" not in said.split("changed")[0][-40:], "never how many were sent"


def test_only_ids_are_only_ids(client, user, stranger):
    """Nothing but a primary key is read out of the list, so nothing else can be smuggled."""
    theirs = an_application(stranger, "Theirs")
    tag = Tag.objects.create(owner=user, name="shortlist")
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [str(theirs.pk), "not-a-number", ""], "bulk-action": "tag", "tag": tag.pk},
    )

    assert theirs.tags.count() == 0


def test_somebody_else_s_tag_cannot_be_applied(client, user, stranger):
    """The companion field is re-scoped too, or a bulk action becomes a way to read one."""
    mine = an_application(user)
    theirs = Tag.objects.create(owner=stranger, name="theirs")
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk], "bulk-action": "tag", "tag": theirs.pk},
    )

    assert mine.tags.count() == 0


def test_somebody_else_s_industry_cannot_be_applied(client, user, stranger):
    mine = Company.objects.create(owner=user, name="Mine")
    theirs = Industry.objects.create(owner=stranger, name="theirs")
    client.force_login(user)

    client.post(
        reverse("jobs:company_bulk"),
        {"chosen": [mine.pk], "bulk-action": "industry", "industry": theirs.pk},
    )

    assert mine.industries.count() == 0


def test_a_foreign_company_is_not_touched(client, user, stranger):
    mine = Company.objects.create(owner=user, name="Mine")
    theirs = Company.objects.create(owner=stranger, name="Theirs")
    industry = Industry.objects.create(owner=user, name="Publishing")
    client.force_login(user)

    client.post(
        reverse("jobs:company_bulk"),
        {"chosen": [mine.pk, theirs.pk], "bulk-action": "industry", "industry": industry.pk},
    )

    assert mine.industries.count() == 1
    assert theirs.industries.count() == 0


# ------------------------------------------------------------ what it will not do


def test_there_is_no_bulk_delete(client, user):
    """Deleting forty things is a different act, and wants its own confirmation."""
    mine = an_application(user)
    client.force_login(user)

    client.post(reverse("applications:bulk"), {"chosen": [mine.pk], "bulk-action": "delete"})

    assert Application.objects.filter(pk=mine.pk).exists()


def test_an_unknown_action_does_nothing_rather_than_guessing(client, user):
    mine = an_application(user)
    tag = Tag.objects.create(owner=user, name="shortlist")
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk], "bulk-action": "nonsense", "tag": tag.pk},
    )

    assert mine.tags.count() == 0


def test_signing_out_shuts_it(client, user):
    mine = an_application(user)

    response = client.post(reverse("applications:bulk"), {"chosen": [mine.pk]})

    assert response.status_code in (302, 403)
    assert "login" in response["Location"] if response.status_code == 302 else True


def test_a_very_long_list_is_bounded(client, user):
    """A list from the client is not a promise; a hundred thousand ids is one request."""
    request = type("R", (), {"POST": None})()
    from django.http import QueryDict

    query = QueryDict(mutable=True)
    query.setlist(bulk.CHOSEN, [str(n) for n in range(bulk.MAX_CHOSEN + 500)])
    request.POST = query

    assert len(bulk.chosen_ids(request)) == bulk.MAX_CHOSEN


def test_repeats_are_counted_once():
    from django.http import QueryDict

    request = type("R", (), {"POST": None})()
    query = QueryDict(mutable=True)
    query.setlist(bulk.CHOSEN, ["7", "7", "7"])
    request.POST = query

    assert bulk.chosen_ids(request) == [7]


# ------------------------------------------------------- the event log is the truth


def test_a_status_change_writes_a_timeline_entry(client, user):
    """Forty applications quietly moved would be forty records that cannot say when."""
    mine = an_application(user)
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk], "bulk-action": "status", "status": Status.APPLIED},
    )

    mine.refresh_from_db()
    assert mine.status == Status.APPLIED
    assert mine.events.exists(), "moved through change_status, not through update()"


def test_a_status_that_is_not_one_changes_nothing(client, user):
    mine = an_application(user)
    client.force_login(user)

    client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk], "bulk-action": "status", "status": "invented"},
    )

    mine.refresh_from_db()
    assert mine.status == Status.DRAFT


def test_moving_something_to_the_status_it_already_has_is_not_a_change(client, user):
    mine = an_application(user)
    client.force_login(user)

    response = client.post(
        reverse("applications:bulk"),
        {"chosen": [mine.pk], "bulk-action": "status", "status": Status.DRAFT},
        follow=True,
    )

    assert "0 applications changed" in response.content.decode()


# ------------------------------------------------------------------- nothing ticked


def test_ticking_nothing_says_so(client, user):
    client.force_login(user)

    response = client.post(reverse("applications:bulk"), {"bulk-action": "tag"}, follow=True)

    assert "Nothing was selected" in response.content.decode()
