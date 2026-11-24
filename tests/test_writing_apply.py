"""Putting one suggested replacement into a letter, and taking it out again (#1258).

One press, one replacement, by the person who read it: a POST that says where the words are
and what goes there, refused when the letter is no longer the text the list was drawn from.
The checker is not asked again, and nothing is ever replaced by a page being looked at.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from postulo.documents import checking
from postulo.documents.models import CoverLetter
from tests import writing

BODY = "Dear {{ compnay }},\r\n\r\nI have led the the team for 😀 years.\r\n\r\n{{ name }}"


@pytest.fixture(autouse=True)
def _fresh_registry():
    writing.forget()
    yield
    writing.forget()


@pytest.fixture
def letter(db, user):
    return CoverLetter.objects.create(owner=user, name="General", subject="About it", body=BODY)


@pytest.fixture
def doubled(monkeypatch):
    """A plugin that proposes the word once where "the the" is written."""

    def check(document):
        text = document.passage("body").text
        at = text.find("the the")
        if at >= 0:
            yield writing.finding(
                check="repeated-word",
                message="“the” is written twice in a row.",
                start=at,
                end=at + 7,
                replacements=("the",),
            )

    writing.install(monkeypatch, writing.plugin("mine", writing.checker(check)))


def address(letter) -> str:
    return reverse("documents:letter_apply", args=[letter.pk])


def fields(letter, old: str, new: str, **changes) -> dict:
    start = letter.body.index(old)
    posted = {
        "passage": "body",
        "start": start,
        "end": start + len(old),
        "digest": checking.digest(letter.body),
        "replacement": json.dumps(new),
    }
    posted.update(changes)
    return posted


def said(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


# ------------------------------------------------------------------------ replacing


def test_the_pages_own_button_replaces_exactly_what_it_named(client, user, letter, doubled):
    """Through the form the page drew, as a browser with scripts off posts it: after Windows
    line endings and a character outside the basic plane, the span is still where it said."""
    client.force_login(user)
    page = client.get(letter.get_absolute_url()).content.decode()
    typo, repeated = writing.forms(page)
    assert "Replace “<bdi" in writing.section(page), "the button says what it does"

    response = client.post(address(letter), repeated)

    letter.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"] == f"{letter.get_absolute_url()}#writing-findings"
    assert letter.body == BODY.replace("the the", "the")
    assert json.loads(typo["replacement"]) == "{{ company }}"


def test_the_button_is_named_by_what_it_replaces_with_what(client, user, letter, doubled):
    client.force_login(user)

    drawn = writing.section(client.get(letter.get_absolute_url()).content.decode())

    assert (
        'Replace “<bdi lang="en-GB" dir="ltr" class="whitespace-pre-wrap">the the</bdi>” '
        'with “<bdi lang="en-GB" dir="ltr" class="whitespace-pre-wrap">the</bdi>”'
    ) in drawn


def test_the_page_says_what_was_replaced_and_offers_to_undo_it(client, user, letter, doubled):
    client.force_login(user)

    response = client.post(address(letter), fields(letter, "the the", "the"), follow=True)
    page = response.content.decode()

    assert 'role="status"' in page and "Replaced “<bdi" in page
    assert "data-writing-undo" in writing.section(page)
    assert "Undo the last replacement" in writing.section(page)
    assert "1 thing will go out wrong." in writing.section(page), "the count is of what is left"


def test_a_misspelt_placeholder_is_put_right_whole(client, user, letter):
    client.force_login(user)

    client.post(address(letter), fields(letter, "{{ compnay }}", "{{ company }}"))

    letter.refresh_from_db()
    assert letter.body.startswith("Dear {{ company }},\r\n")


def test_a_replacement_keeps_its_own_line_break_as_it_is(client, user, letter):
    """A hidden field's line break would arrive as a browser normalised it; JSON does not."""
    client.force_login(user)

    client.post(address(letter), fields(letter, " for 😀 years.", ".\nSince then"))

    letter.refresh_from_db()
    assert "team.\nSince then\r\n" in letter.body


def test_the_subject_can_be_replaced_in_too(client, user, letter):
    client.force_login(user)

    client.post(
        address(letter),
        {
            "passage": "subject",
            "start": 6,
            "end": 8,
            "digest": checking.digest(letter.subject),
            "replacement": json.dumps("the role"),
        },
    )

    letter.refresh_from_db()
    assert letter.subject == "About the role"


# -------------------------------------------------------------------------- refused


def test_a_letter_that_has_changed_since_is_left_alone(client, user, letter):
    client.force_login(user)
    posted = fields(letter, "the the", "the")
    letter.body = BODY.replace("Dear", "Hello")
    letter.save()

    response = client.post(address(letter), posted)

    letter.refresh_from_db()
    assert letter.body == BODY.replace("Dear", "Hello"), "nothing was replaced"
    assert "has changed since" in said(response)[0]
    assert "writing_undo" not in client.session


@pytest.mark.parametrize(
    "changes",
    [
        {"end": 100_000},  # past the end
        {"start": "-1"},
        {"start": "abc"},
        {"start": 8, "end": 12},  # half of {{ compnay }}
        {"passage": "name"},  # not one of a letter's passages
        {"passage": ""},
        {"replacement": "not json"},
        {"replacement": json.dumps(["a", "list"])},
        {"replacement": json.dumps("x" * 501)},
        {"replacement": json.dumps("the the")},  # what is already there
        {"digest": ""},
    ],
)
def test_a_replacement_that_breaks_a_rule_changes_nothing(client, user, letter, changes):
    client.force_login(user)

    response = client.post(address(letter), fields(letter, "the the", "the", **changes))

    letter.refresh_from_db()
    assert response.status_code == 302
    assert (letter.subject, letter.body) == ("About it", BODY)
    assert said(response) and "Replaced" not in said(response)[0]
    assert "writing_undo" not in client.session


def test_a_line_break_is_not_put_into_the_subject(client, user, letter):
    client.force_login(user)

    client.post(
        address(letter),
        {
            "passage": "subject",
            "start": 0,
            "end": 5,
            "digest": checking.digest(letter.subject),
            "replacement": json.dumps("On\nthis"),
        },
    )

    letter.refresh_from_db()
    assert letter.subject == "About it"


def test_a_replacement_that_would_overfill_the_subject_is_refused(client, user, letter):
    letter.subject = "x" * 249
    letter.save()
    client.force_login(user)

    client.post(
        address(letter),
        {
            "passage": "subject",
            "start": 249,
            "end": 249,
            "digest": checking.digest(letter.subject),
            "replacement": json.dumps("yz"),
        },
    )

    letter.refresh_from_db()
    assert letter.subject == "x" * 249


def test_a_replacement_that_would_empty_the_body_is_refused(client, user):
    short = CoverLetter.objects.create(owner=user, name="Short", body="Hello")
    client.force_login(user)

    client.post(
        address(short),
        {
            "passage": "body",
            "start": 0,
            "end": 5,
            "digest": checking.digest("Hello"),
            "replacement": json.dumps(""),
        },
    )

    short.refresh_from_db()
    assert short.body == "Hello"


def test_it_answers_a_post_and_nothing_else(client, user, letter):
    client.force_login(user)

    assert client.get(address(letter)).status_code == 405
    letter.refresh_from_db()
    assert letter.body == BODY


def test_the_checker_is_not_asked_again(client, user, letter, monkeypatch):
    """What goes in is what the button said, whatever a checker would say now."""
    asked = []
    writing.install(
        monkeypatch, writing.plugin("mine", writing.checker(lambda d: asked.append(d) or []))
    )
    client.force_login(user)

    client.post(address(letter), fields(letter, "the the", "the"))

    letter.refresh_from_db()
    assert letter.body == BODY.replace("the the", "the")
    assert asked == []


# ----------------------------------------------------------------------------- undo


def test_undo_puts_back_exactly_what_was_there(client, user, letter):
    client.force_login(user)
    client.post(address(letter), fields(letter, "the the", "the"))

    response = client.post(address(letter), {"undo": "1"}, follow=True)

    letter.refresh_from_db()
    assert letter.body == BODY
    assert "Put back as it was." in response.content.decode()
    assert "data-writing-undo" not in response.content.decode(), "there is one undo, not a stack"


def test_undo_after_the_letter_changed_puts_nothing_back(client, user, letter):
    client.force_login(user)
    client.post(address(letter), fields(letter, "the the", "the"))
    letter.refresh_from_db()
    letter.body += "\r\nP.S."
    letter.save()

    page = client.get(letter.get_absolute_url()).content.decode()
    response = client.post(address(letter), {"undo": "1"})

    letter.refresh_from_db()
    assert "data-writing-undo" not in page, "the button is not offered for a text that moved on"
    assert letter.body.endswith("P.S.") and "the the" not in letter.body
    assert "nothing to put back" in said(response)[0]


def test_undo_with_nothing_kept_changes_nothing(client, user, letter):
    client.force_login(user)

    response = client.post(address(letter), {"undo": "1"})

    letter.refresh_from_db()
    assert letter.body == BODY
    assert "nothing to put back" in said(response)[0]


def test_one_letters_undo_is_not_offered_on_another(client, user, letter):
    other = CoverLetter.objects.create(owner=user, name="Other", body=BODY)
    client.force_login(user)
    client.post(address(letter), fields(letter, "the the", "the"))

    page = client.get(other.get_absolute_url()).content.decode()
    client.post(address(other), {"undo": "1"})

    other.refresh_from_db()
    assert "data-writing-undo" not in page
    assert other.body == BODY
