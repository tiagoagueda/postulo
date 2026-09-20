"""Slow work sent off to be done, and the page that watches it (#247).

The decisions worth a test here are the ones that were taken rather than inherited: that an
instance with no worker keeps working exactly as it did, that a queue which refuses the work
does not lose it, that the rate limit is still spent by asking rather than by doing, that a
poll is ownership-checked as firmly as the press was, and that nothing is left on disk.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.core import errands
from postulo.core.models import Errand, ErrandState

pytestmark = pytest.mark.django_db


@pytest.fixture
def a_kind():
    """A handler registered for the length of one test, and taken out again."""
    done: list[int] = []

    @errands.handler("test_kind", working="Doing the thing…")
    def run(errand) -> dict:
        done.append(errand.pk)
        if errand.payload.get("explode"):
            raise ValueError("a bug, not an explanation")
        if errand.payload.get("refuse"):
            raise errands.Refused("That site said no.")
        return {"message": "It is done.", "url": "/somewhere/"}

    run.done = done
    yield run
    errands.HANDLERS.pop("test_kind", None)


# ------------------------------------------------------- the queue is optional


def test_with_no_worker_the_work_is_done_where_it_stands(a_kind, user, settings):
    """The default, and the shape a personal instance runs in: one container, no queue."""
    settings.POSTULO_BACKGROUND_WORK = False

    errand = errands.send("test_kind", user)

    assert a_kind.done == [errand.pk], "done before the caller got its answer"
    assert errand.state == ErrandState.DONE
    assert errand.message == "It is done." and errand.url == "/somewhere/"
    assert errand.finished_at is not None


def test_with_a_worker_the_work_waits_for_it(a_kind, user, settings):
    settings.POSTULO_BACKGROUND_WORK = True

    errand = errands.send("test_kind", user)

    assert a_kind.done == [], "nothing was done in the request"
    assert errand.state == ErrandState.WAITING

    errands.perform(errand.pk)
    errand.refresh_from_db()
    assert a_kind.done == [errand.pk] and errand.state == ErrandState.DONE


def test_a_queue_that_refuses_the_work_does_not_lose_it(a_kind, user, settings, monkeypatch):
    """An operator who set the flag and never started the container still gets their work.

    The alternative is an errand left *waiting* for ever, which is the silent button this
    whole arrangement exists to avoid.
    """
    settings.POSTULO_BACKGROUND_WORK = True

    class NoQueue:
        # The whole task object, because `Task` is a frozen dataclass and its `enqueue`
        # cannot be replaced on the instance.
        def enqueue(self, *args, **kwargs):
            raise RuntimeError("no queue here")

    monkeypatch.setattr("postulo.core.tasks.perform_errand", NoQueue())

    errand = errands.send("test_kind", user)

    assert errand.state == ErrandState.DONE and a_kind.done == [errand.pk]


def test_an_unknown_kind_is_refused_at_the_door(user):
    with pytest.raises(KeyError):
        errands.send("no_such_kind", user)


# ------------------------------------------------------------ how it can go wrong


def test_a_refusal_is_shown_in_the_words_it_was_raised_with(a_kind, user):
    errand = errands.send("test_kind", user, refuse=True)

    assert errand.state == ErrandState.FAILED
    assert errand.message == "That site said no."


def test_a_bug_is_logged_and_reported_as_a_sentence(a_kind, user, caplog):
    """A person watching a spinner cannot act on a traceback; the log can."""
    errand = errands.send("test_kind", user, explode=True)

    assert errand.state == ErrandState.FAILED
    assert "Something went wrong" in errand.message
    assert "a bug, not an explanation" not in errand.message
    assert any("failed" in record.message for record in caplog.records)


def test_a_kind_this_version_no_longer_has_says_so(user):
    """Queued by a version that had it, run by a version that does not."""
    errand = Errand.objects.create(kind="retired_kind", owner=user)

    errands.perform(errand.pk)

    errand.refresh_from_db()
    assert errand.state == ErrandState.FAILED
    assert "no longer" in errand.message


def test_an_errand_whose_row_has_gone_is_not_an_error(a_kind, user, settings):
    settings.POSTULO_BACKGROUND_WORK = True
    errand = errands.send("test_kind", user)
    errand.delete()

    errands.perform(errand.pk)  # there is nobody left to tell

    assert a_kind.done == []


def test_work_already_finished_is_not_done_twice(a_kind, user, settings):
    """A worker that claims a row twice would otherwise file two of whatever it makes."""
    settings.POSTULO_BACKGROUND_WORK = False
    errand = errands.send("test_kind", user)

    errands.perform(errand.pk)

    assert a_kind.done == [errand.pk], "once, not twice"


# --------------------------------------------------------------- watching it


def test_the_page_says_what_is_happening_and_where_to_go(a_kind, client, user):
    client.force_login(user)
    errand = errands.send("test_kind", user)

    html = client.get(reverse("core:errand", args=[errand.pk])).content.decode()

    assert "It is done." in html and "/somewhere/" in html
    assert 'aria-live="polite"' in html, "the change is announced, not merely painted"


def test_an_unfinished_page_polls_and_a_finished_one_stops(a_kind, client, user, settings):
    settings.POSTULO_BACKGROUND_WORK = True
    client.force_login(user)
    errand = errands.send("test_kind", user)
    state_url = reverse("core:errand_state", args=[errand.pk])

    waiting = client.get(state_url).content.decode()
    assert "hx-trigger" in waiting and "Doing the thing" in waiting
    assert "Check again" in waiting, "and it is watchable with no script at all"

    errands.perform(errand.pk)
    finished = client.get(state_url).content.decode()
    assert "hx-trigger" not in finished, "a finished fragment asks for nothing"


def test_the_poll_is_ownership_checked_not_only_the_press(a_kind, client, user, other_user):
    """A poll is a new address that names a piece of work, which is an address somebody
    will try with another row's number."""
    errand = errands.send("test_kind", user)
    client.force_login(other_user)

    assert client.get(reverse("core:errand", args=[errand.pk])).status_code == 404
    assert client.get(reverse("core:errand_state", args=[errand.pk])).status_code == 404


def test_watching_needs_signing_in(a_kind, client, user):
    errand = errands.send("test_kind", user)

    assert client.get(reverse("core:errand_state", args=[errand.pk])).status_code == 302


# ------------------------------------------------------------- what is left behind


def test_finished_errands_are_forgotten_after_a_week(a_kind, user):
    old = errands.send("test_kind", user)
    Errand.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(days=8))
    recent = errands.send("test_kind", user)

    errands.forget_old()

    assert not Errand.objects.filter(pk=old.pk).exists()
    assert Errand.objects.filter(pk=recent.pk).exists()


def test_a_subject_deleted_while_it_waited_leaves_the_errand_behind(a_kind, user, settings):
    """What happened still happened, and the page asking about it deserves an answer."""
    from postulo.jobs.models import Company

    settings.POSTULO_BACKGROUND_WORK = True
    company = Company.objects.create(owner=user, name="Aperture Science")
    errand = errands.send("test_kind", user, subject=company)

    company.delete()

    errand.refresh_from_db()
    assert errand.subject is None
    assert errand.state == ErrandState.WAITING, "the errand outlives what it was about"
