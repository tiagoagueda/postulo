"""Putting a replacement into a letter is a write, and a letter is one person's (#1258).

The apply view takes a letter's id from the address and text from the form, and writes it
into stored words. So: somebody else's letter is a 404 and stays as it was, a signed-out
request goes to sign-in, the way back is never off this site, an application that is not
the requester's fills in nothing in the list, and what a checker is handed and what the log
keeps never carry more than they should.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from postulo.documents import checking
from postulo.documents.models import CoverLetter
from tests import writing

pytestmark = pytest.mark.django_db

BODY = "Dear {{ company }},\n\nI have led the the team.\n"


@pytest.fixture(autouse=True)
def _fresh_registry():
    writing.forget()
    yield
    writing.forget()


@pytest.fixture
def theirs(other_user):
    return CoverLetter.objects.create(owner=other_user, name="Theirs", body=BODY)


def replacing(letter) -> dict:
    start = letter.body.index("the the")
    return {
        "passage": "body",
        "start": start,
        "end": start + 7,
        "digest": checking.digest(letter.body),
        "replacement": json.dumps("the"),
    }


def test_somebody_elses_letter_is_a_404_and_is_not_changed(client, user, theirs):
    client.force_login(user)

    response = client.post(reverse("documents:letter_apply", args=[theirs.pk]), replacing(theirs))

    theirs.refresh_from_db()
    assert response.status_code == 404, "never a 403: that would say the letter exists"
    assert theirs.body == BODY


def test_somebody_elses_letter_cannot_be_undone_into_either(client, user, other_user, theirs):
    """The record *Undo* reads is the requester's own session's, and names a letter: one
    kept for the requester's letter puts nothing back into another person's."""
    mine = CoverLetter.objects.create(owner=user, name="Mine", body=BODY)
    client.force_login(user)
    client.post(reverse("documents:letter_apply", args=[mine.pk]), replacing(mine))
    session = client.session
    session["writing_undo"] = {**session["writing_undo"], "letter": theirs.pk}
    session.save()

    response = client.post(reverse("documents:letter_apply", args=[theirs.pk]), {"undo": "1"})

    theirs.refresh_from_db()
    assert response.status_code == 404
    assert theirs.body == BODY


def test_a_signed_out_request_goes_to_sign_in_and_changes_nothing(client, theirs):
    response = client.post(reverse("documents:letter_apply", args=[theirs.pk]), replacing(theirs))

    theirs.refresh_from_db()
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]
    assert theirs.body == BODY


@pytest.mark.parametrize(
    "target",
    ["https://evil.example/letters/", "//evil.example/", "/\\evil.example", "account_login"],
)
def test_the_way_back_never_leaves_this_site(client, user, target):
    mine = CoverLetter.objects.create(owner=user, name="Mine", body=BODY)
    client.force_login(user)

    response = client.post(
        reverse("documents:letter_apply", args=[mine.pk]), {**replacing(mine), "next": target}
    )

    assert response["Location"] == f"{mine.get_absolute_url()}#writing-findings"


def test_the_way_back_keeps_the_application_and_lands_on_the_list(client, user):
    mine = CoverLetter.objects.create(owner=user, name="Mine", body=BODY)
    client.force_login(user)
    back = f"{mine.get_absolute_url()}?application=7#somewhere-else"

    response = client.post(
        reverse("documents:letter_apply", args=[mine.pk]), {**replacing(mine), "next": back}
    )

    assert response["Location"] == f"{mine.get_absolute_url()}?application=7#writing-findings"


def test_the_list_is_never_drawn_from_somebody_elses_application(
    client, user, other_user, monkeypatch
):
    """A checker is handed the facts of the application chosen. An application that is not
    the requester's is no application: its company never reaches a checker or the page."""
    from postulo.applications.models import Application
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=other_user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=other_user, company=company, title="Tester")
    foreign = Application.objects.create(owner=other_user, posting=posting)
    mine = CoverLetter.objects.create(owner=user, name="Mine", body=BODY)
    handed = []
    writing.install(
        monkeypatch, writing.plugin("mine", writing.checker(lambda d: handed.append(d) or []))
    )
    client.force_login(user)

    page = client.get(mine.get_absolute_url(), {"application": foreign.pk}).content.decode()

    assert "Aperture" not in page
    assert handed and all(not document.facts for document in handed)
    assert "Aperture" not in repr(handed)


def test_another_persons_letter_page_is_a_404_whatever_checker_is_installed(
    client, user, theirs, monkeypatch
):
    handed = []
    writing.install(
        monkeypatch, writing.plugin("mine", writing.checker(lambda d: handed.append(d) or []))
    )
    client.force_login(user)

    assert client.get(theirs.get_absolute_url()).status_code == 404
    assert handed == [], "no checker was handed a word of it"
