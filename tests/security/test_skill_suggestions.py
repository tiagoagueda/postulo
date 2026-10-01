"""The skill box's address, which answers what somebody types with names (#266).

It names no record, so the sweep next door has nothing to sweep, and what it answers is the
ESCO classification's, which belongs to nobody. What there is to hold instead:

- nobody reaches it without an account, and it answers nothing but a read;
- what was typed is bounded, and never comes back as markup;
- the one thing read of a person -- which languages to answer in -- is the asker's own;
- how often it may be asked is one account's allowance (`test_esco_skills.py` counts it).
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse

pytestmark = pytest.mark.django_db

URL = "resume:skill_suggestions"


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


def test_nobody_is_answered_without_an_account(esco_skills, client):
    answer = client.get(reverse(URL), {"name": "proj"})

    assert answer.status_code == 302
    assert reverse("account_login") in answer["Location"]
    assert b"<option" not in answer.content


def test_it_answers_a_read_and_nothing_else(esco_skills, user, client):
    client.force_login(user)

    for method in (client.post, client.put, client.delete):
        assert method(reverse(URL), {"name": "proj"}).status_code == 405


def test_what_was_typed_never_comes_back_as_markup(esco_skills, user, client):
    client.force_login(user)

    answer = client.get(reverse(URL), {"name": '"><script>alert(1)</script>'})

    assert answer.status_code == 200
    assert b"<script" not in answer.content and b"alert" not in answer.content


def test_what_was_typed_is_bounded(esco_skills, user, client):
    client.force_login(user)

    answer = client.get(reverse(URL), {"name": "pr" + "o" * 100_000})

    assert answer.status_code == 200 and b"<option" not in answer.content


def test_the_languages_answered_in_are_the_askers_own(esco_skills, user, other_user, client):
    """Somebody else's record being in Portuguese is not a reason to answer in it."""
    other_user.profile.record_language = "pt-PT"
    other_user.profile.save()
    user.profile.record_language = "en-GB"
    user.profile.save()
    client.force_login(user)

    answer = client.get(reverse(URL), {"name": "ge"}).content.decode()

    assert "gestão" not in answer and "gerir" not in answer
