"""The first account becomes the administrator exactly once, and the log says so (#738)."""

from __future__ import annotations

import logging

import pytest
from allauth.account.signals import user_signed_up
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.db.models.query import QuerySet
from django.test import RequestFactory

from postulo.core.models import SiteSettings

pytestmark = pytest.mark.django_db

User = get_user_model()


def sign_up(email):
    user = User.objects.create_user(
        email=email, password="a-fairly-long-password-42", username=email.split("@")[0]
    )
    request = RequestFactory().get("/")
    SessionMiddleware(lambda r: None).process_request(request)
    user_signed_up.send(sender=User, request=request, user=user)
    user.refresh_from_db()
    return user


def test_the_first_is_the_administrator_and_the_second_is_not(caplog):
    with caplog.at_level(logging.WARNING, logger="postulo.accounts.signals"):
        first = sign_up("first@example.org")
        second = sign_up("second@example.org")

    assert (first.is_staff, first.is_superuser) == (True, True)
    assert (second.is_staff, second.is_superuser) == (False, False)
    warnings = [r for r in caplog.records if "first account" in r.getMessage()]
    assert len(warnings) == 1 and str(first.pk) in warnings[0].getMessage()


def test_the_decision_is_taken_under_a_lock_on_the_settings_row(monkeypatch):
    locked = []
    original = QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        if self.model is SiteSettings:
            locked.append(self.model)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", spy)

    sign_up("first@example.org")

    assert locked == [SiteSettings]
