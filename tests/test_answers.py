"""Answers kept for application forms, and the copy each application keeps (#1092).

Application forms ask the same questions again and again, and the answers were typed into a
browser and lost. What is held here: a saved answer is the person's own, with a language and
tags; using one on an application makes a copy with the application's details in its
placeholders, and from then on the two are independent; the copies leave with the export and
come back; and the API reads both and nothing of anybody else's.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
from django.urls import reverse

from postulo.applications import answers
from postulo.applications.models import Application, ApplicationAnswer, SavedAnswer, Status
from postulo.core import export as export_module
from postulo.core import importer
from postulo.core.models import Tag
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Test Engineer", location="Lisbon"
    )
    return Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)


@pytest.fixture
def why(user):
    return SavedAnswer.objects.create(
        owner=user,
        question="Why {{ company }}?",
        answer="I want to be a {{ role }} at {{ company }}, in {{ location }}. {{ contact }}",
        language="en-GB",
    )


def read_archive(user):
    archive = zipfile.ZipFile(export_module.write_archive(user))
    return archive, json.loads(archive.read(export_module.MANIFEST_NAME))


def restored(document, user):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as written:
        written.writestr("postulo.json", json.dumps(document))
    buffer.seek(0)
    return importer.load(user, zipfile.ZipFile(buffer))


# ----------------------------------------------------------------- the copy


def test_using_a_saved_answer_fills_the_applications_details_in(application, why):
    initial = answers.initial_from(why, application)

    assert initial["answer"].startswith(
        "I want to be a Test Engineer at Aperture Science, in Lisbon."
    )
    # A placeholder with nothing to fill it stays, so the person sees it and writes the word.
    assert initial["answer"].endswith("{{ contact }}")
    assert initial["language"] == "en-GB"


def test_a_copy_is_independent_of_the_saved_answer_it_began_as(client, user, application, why):
    client.force_login(user)
    url = reverse("applications:answer_given_create", args=[application.pk])

    page = client.get(url, {"from": why.pk})
    assert page.status_code == 200
    assert "Why {{ company }}?" in page.content.decode()

    response = client.post(
        url,
        {
            "question": "Why Aperture?",
            "answer": "Because science.",
            "language": "",
            "saved_answer": why.pk,
        },
    )
    assert response.status_code == 302
    given = ApplicationAnswer.objects.get()
    assert (given.application, given.saved_answer, given.owner) == (application, why, user)

    why.answer = "Changed later."
    why.save()
    assert ApplicationAnswer.objects.get().answer == "Because science."

    client.post(
        reverse("applications:answer_given_update", args=[given.pk]),
        {"question": "Why Aperture?", "answer": "Edited on the application.", "language": ""},
    )
    assert SavedAnswer.objects.get().answer == "Changed later."

    why.delete()
    given.refresh_from_db()
    assert given.saved_answer is None and given.answer == "Edited on the application."


def test_the_application_page_lists_what_was_answered(client, user, application):
    answers.give(application, question="Notice period?", answer="Two months", language="")
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    assert "What I answered" in page and "Notice period?" in page and "Two months" in page


# ------------------------------------------------------------ the saved ones


def test_a_saved_answer_is_made_with_its_tags_and_found_again(client, user):
    client.force_login(user)
    response = client.post(
        reverse("resume:answer_create"),
        {
            "question": "Salary expectation",
            "answer": "Between 50 and 60 thousand.",
            "language": "pt-PT",
            "new_tags": "pay, money",
        },
    )
    assert response.status_code == 302
    saved = SavedAnswer.objects.get()
    assert (saved.owner, saved.language) == (user, "pt-PT")
    assert {tag.name for tag in saved.tags.all()} == {"pay", "money"}

    listed = client.get(reverse("resume:answer_list"), {"q": "salary"}).content.decode()
    assert "Salary expectation" in listed
    nothing = client.get(reverse("resume:answer_list"), {"q": "zebra"}).content.decode()
    assert "Salary expectation" not in nothing
    tagged = client.get(reverse("resume:answer_list"), {"tag": "pay"}).content.decode()
    assert "Salary expectation" in tagged


def test_a_saved_answer_needs_its_question_and_its_words(client, user):
    client.force_login(user)
    response = client.post(reverse("resume:answer_create"), {"question": "", "answer": ""})
    assert response.status_code == 200
    assert not SavedAnswer.objects.exists()


# ------------------------------------------------------------- whose they are


def test_nobody_reaches_somebody_elses_answers(client, user, other_user, why):
    mine = Company.objects.create(owner=user, name="Mine")
    posting = JobPosting.objects.create(owner=user, company=mine, title="Role")
    theirs = Application.objects.create(owner=user, posting=posting)
    given = answers.give(theirs, question="Q", answer="A", language="")
    client.force_login(other_user)

    for name, args in (
        ("resume:answer_update", [why.pk]),
        ("resume:answer_delete", [why.pk]),
        ("applications:answer_given_update", [given.pk]),
        ("applications:answer_given_delete", [given.pk]),
    ):
        url = reverse(name, args=args)
        assert client.get(url).status_code == 404
        assert client.post(url).status_code == 404
    assert (
        client.get(reverse("applications:answer_given_create", args=[theirs.pk])).status_code == 404
    )
    assert "Why" not in client.get(reverse("resume:answer_list")).content.decode()
    assert SavedAnswer.objects.filter(pk=why.pk).exists()
    assert ApplicationAnswer.objects.filter(pk=given.pk).exists()


def test_an_application_cannot_begin_from_somebody_elses_saved_answer(
    client, user, other_user, application
):
    theirs = SavedAnswer.objects.create(owner=other_user, question="Secret", answer="Salary 99")
    client.force_login(user)
    url = reverse("applications:answer_given_create", args=[application.pk])

    page = client.get(url, {"from": theirs.pk}).content.decode()
    assert "Salary 99" not in page and "Secret" not in page

    client.post(url, {"question": "Q", "answer": "A", "saved_answer": theirs.pk})
    assert ApplicationAnswer.objects.get().saved_answer is None


# ------------------------------------------------------------------ the archive


def test_the_answers_survive_the_round_trip(user, other_user, application, why):
    why.tags.add(Tag.named(user, ["pay"])[0])
    answers.give(
        application, question="Why Aperture?", answer="Science.", language="en-GB", saved=why
    )

    _archive, document = read_archive(user)
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION >= 85
    assert [row["question"] for row in document["saved_answers"]] == ["Why {{ company }}?"]
    assert document["saved_answers"][0]["tags"] == ["pay"]

    report = restored(document, other_user)

    saved = SavedAnswer.objects.for_user(other_user).get()
    given = ApplicationAnswer.objects.for_user(other_user).get()
    assert (saved.question, saved.language) == ("Why {{ company }}?", "en-GB")
    assert [tag.name for tag in saved.tags.all()] == ["pay"]
    assert (given.answer, given.saved_answer, given.language) == ("Science.", saved, "en-GB")
    assert given.application.owner == other_user
    assert (report.saved_answers, report.answers) == (1, 1)


def test_an_archive_from_before_answers_still_loads(user, other_user, application, why):
    _archive, document = read_archive(user)
    document["postulo"]["format"] = 84
    del document["saved_answers"]
    for company in document["companies"]:
        for posting in company["postings"]:
            for entry in posting["applications"]:
                del entry["answers"]

    restored(document, other_user)

    assert Application.objects.for_user(other_user).count() == 1
    assert not SavedAnswer.objects.for_user(other_user).exists()


def test_an_answer_with_no_question_is_left_out_and_said(user, other_user, application, why):
    answers.give(application, question="Q", answer="A", language="")
    _archive, document = read_archive(user)
    document["saved_answers"][0]["question"] = ""
    document["companies"][0]["postings"][0]["applications"][0]["answers"][0]["question"] = "x" * 400

    report = restored(document, other_user)

    assert not SavedAnswer.objects.for_user(other_user).exists()
    assert not ApplicationAnswer.objects.for_user(other_user).exists()
    assert (
        len([line for line in report.skipped if "left it out" in line or "left out" in line]) >= 2
    )


# ----------------------------------------------------------------------- the API


def token_for(user, scopes="read"):
    from postulo.api.models import ApiToken

    _token, raw = ApiToken.issue(owner=user, name="test", scopes=scopes.split())
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_reads_saved_answers_and_what_applications_gave(
    client, user, other_user, application, why
):
    SavedAnswer.objects.create(owner=other_user, question="Theirs", answer="No")
    why.tags.add(Tag.named(user, ["motivation"])[0])
    given = answers.give(application, question="Q", answer="A", language="", saved=why)
    headers = token_for(user)

    listed = client.get("/api/v1/answers", **headers).json()["items"]
    assert [(row["id"], row["tags"]) for row in listed] == [(why.pk, ["motivation"])]
    assert client.get(f"/api/v1/answers/{why.pk}", **headers).json()["language"] == "en-GB"
    only = client.get("/api/v1/answers", {"tag": "nothing"}, **headers).json()["items"]
    assert only == []

    done = client.get("/api/v1/answers/given", {"application": application.pk}, **headers)
    assert [(r["id"], r["saved_answer_id"]) for r in done.json()["items"]] == [(given.pk, why.pk)]
    assert client.get(f"/api/v1/answers/given/{given.pk}", **headers).json()["answer"] == "A"


def test_the_api_does_not_show_somebody_elses_answers(client, user, other_user):
    theirs = SavedAnswer.objects.create(owner=other_user, question="Theirs", answer="No")
    headers = token_for(user)

    assert client.get(f"/api/v1/answers/{theirs.pk}", **headers).status_code == 404
    assert client.get("/api/v1/answers/given/1", **headers).status_code == 404
    assert client.get("/api/v1/answers").status_code == 401


def test_a_contact_is_named_in_the_copy(user, application):
    application.contact = Contact.objects.create(
        owner=user, company=application.posting.company, name="Cave"
    )
    application.save()
    saved = SavedAnswer.objects.create(owner=user, question="Q", answer="Dear {{ contact }}")

    assert answers.initial_from(saved, application)["answer"] == "Dear Cave"
