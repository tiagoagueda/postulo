"""Expired sessions, and what they held, are deleted by the scheduler and by the tidy-up (#751)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.utils import timezone

from postulo.accounts import sessions
from postulo.core import slow

pytestmark = pytest.mark.django_db


def a_session(key, *, days):
    return Session.objects.create(
        session_key=key, session_data="x", expire_date=timezone.now() + dt.timedelta(days=days)
    )


def test_only_the_expired_go():
    a_session("old", days=-1)
    a_session("live", days=1)

    assert sessions.clear_expired() == 1
    assert list(Session.objects.values_list("session_key", flat=True)) == ["live"]


def test_the_tidy_up_that_pages_ask_for_clears_them_once_an_hour():
    cache.delete("core:tidied-up")
    a_session("old", days=-1)

    slow.tidy_up_if_due()

    assert not Session.objects.filter(session_key="old").exists()


def test_the_scheduler_pass_clears_them():
    from django.core.management import call_command

    a_session("old", days=-2)

    call_command("send_due_reminders")

    assert not Session.objects.filter(session_key="old").exists()
