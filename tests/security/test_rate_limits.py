"""How often one account may make the server work (#112).

Nothing here is reachable by a stranger — every surface needs an account or a token — and
nothing bounded what somebody holding one could do. Capture is the surface that matters: it
makes Postulo itself issue an outbound request to an address the caller supplies.
`check_destination` already refuses private addresses and revalidates on redirect, so it is
not SSRF; what was missing was a ceiling, without which a self-hosted box becomes a modest
scanner or exhausts its own outbound connections.

The tests set their own rates rather than relying on the shipped defaults, because a test
that breaks when an operator's sensible number changes is testing the number instead of the
mechanism.
"""

from __future__ import annotations

import time

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from postulo.core import throttle

pytestmark = pytest.mark.django_db

PASSWORD = "a-fairly-long-password-42"
#: Enough HTML to be a page and not enough to be a posting: capture is counted before
#: anything is parsed, so what it parses to does not matter here.
NOT_A_POSTING = "<html><body><p>nothing here</p></body></html>"


@pytest.fixture(autouse=True)
def _empty_cache():
    """A counter left by one test must not spend another's allowance."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def person(django_user_model):
    return django_user_model.objects.create_user(
        email="person@example.org", username="person", password=PASSWORD
    )


@pytest.fixture
def other(django_user_model):
    return django_user_model.objects.create_user(
        email="other@example.org", username="other", password=PASSWORD
    )


def capture_once(client) -> str:
    response = client.post(
        reverse("jobs:capture_create"),
        {"url": "https://example.org/jobs/1", "html": NOT_A_POSTING},
        follow=True,
    )
    return response.content.decode()


# ------------------------------------------------------------------- reading a rate


@pytest.mark.parametrize(
    "spec,times,seconds",
    [("30/h", 30, 3600), ("5/m", 5, 60), ("2/s", 2, 1), ("600/d", 600, 86400)],
)
def test_a_rate_is_read_as_written(spec, times, seconds):
    assert throttle.parse(spec) == throttle.Rate(times, seconds)


@pytest.mark.parametrize("spec", ["", None, "0/h", "nonsense", "30", "30/y", "-5/m"])
def test_anything_unreadable_means_no_limit(spec):
    """An operator who mistypes a rate should get a working instance, not a locked one."""
    assert not throttle.parse(spec)


def test_no_limit_lets_everything_through():
    for _ in range(50):
        throttle.consume("anything", "somebody", throttle.UNLIMITED)


# --------------------------------------------------------------------- the mechanism


def test_the_allowance_is_spent_and_then_refused():
    rate = throttle.Rate(3, 60)
    for _ in range(3):
        throttle.consume("thing", "somebody", rate)

    with pytest.raises(throttle.TooOften) as refused:
        throttle.consume("thing", "somebody", rate)

    assert refused.value.retry_after > 0
    assert "3" in str(refused.value)


def test_two_people_do_not_share_an_allowance():
    rate = throttle.Rate(1, 60)
    throttle.consume("thing", "one", rate)

    throttle.consume("thing", "two", rate)  # must not raise


def test_two_actions_do_not_share_an_allowance():
    rate = throttle.Rate(1, 60)
    throttle.consume("first", "somebody", rate)

    throttle.consume("second", "somebody", rate)


def test_the_allowance_comes_back_when_the_window_turns(monkeypatch):
    """A fixed window: it refills at the boundary rather than sliding.

    `throttle._now` rather than `time.time`, because Django's database cache computes its
    expiry from the same clock: freezing it globally makes every entry the cache writes
    already expired, so the limit counts nothing and the test passes for the wrong reason.
    """
    rate = throttle.Rate(1, 60)
    now = time.time()
    monkeypatch.setattr(throttle, "_now", lambda: now)
    throttle.consume("thing", "somebody", rate)
    with pytest.raises(throttle.TooOften):
        throttle.consume("thing", "somebody", rate)

    now += 61
    throttle.consume("thing", "somebody", rate)


# ------------------------------------------------------------------------- capture


@override_settings(POSTULO_CAPTURE_RATE="2/h")
def test_capture_stops_after_its_allowance(client, person):
    client.force_login(person)

    for _ in range(2):
        assert "Too many requests" not in capture_once(client)

    assert "Too many requests" in capture_once(client)


@override_settings(POSTULO_CAPTURE_RATE="1/h")
def test_capture_is_counted_per_account_not_per_address(client, person, other):
    """Sharing an office network must not mean sharing an allowance."""
    client.force_login(person)
    capture_once(client)
    assert "Too many requests" in capture_once(client)

    client.force_login(other)

    assert "Too many requests" not in capture_once(client)


@override_settings(POSTULO_CAPTURE_RATE="1/h")
def test_capture_is_counted_even_when_no_page_is_fetched(client, person, monkeypatch):
    """The HTML came with the request, so nothing goes out — but the parse is still work.

    Counting only the fetch would leave the cheaper half unbounded, and the browser
    extension that will post HTML is the caller most able to do it quickly.
    """

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("nothing should have been fetched")

    monkeypatch.setattr("postulo.jobs.capture_views.fetch_page", must_not_be_called)
    client.force_login(person)
    capture_once(client)

    assert "Too many requests" in capture_once(client)


@override_settings(POSTULO_CAPTURE_RATE="")
def test_an_operator_can_switch_a_limit_off(client, person):
    client.force_login(person)

    for _ in range(6):
        assert "Too many requests" not in capture_once(client)


# ----------------------------------------------------------------------------- api


def token_for(person, name="probe", scopes=("read", "write")):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(owner=person, name=name, scopes=list(scopes))
    return raw


@override_settings(POSTULO_API_RATE="2/h")
def test_the_api_refuses_a_token_that_asks_too_often(client, person):
    raw = token_for(person)
    headers = {"Authorization": f"Bearer {raw}"}

    for _ in range(2):
        assert client.get("/api/v1/me", headers=headers).status_code == 200

    refused = client.get("/api/v1/me", headers=headers)

    assert refused.status_code == 429
    assert "Too many" in refused.content.decode()


@override_settings(POSTULO_API_RATE="1/h")
def test_the_api_counts_per_token_not_per_account(client, person):
    """So a token handed to something that misbehaves can be revoked on its own."""
    first, second = token_for(person, "first"), token_for(person, "second")
    client.get("/api/v1/me", headers={"Authorization": f"Bearer {first}"})
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {first}"}).status_code == 429

    still_fine = client.get("/api/v1/me", headers={"Authorization": f"Bearer {second}"})

    assert still_fine.status_code == 200


@override_settings(POSTULO_API_RATE="1/h")
def test_a_refused_token_is_not_a_wrong_token(client, person):
    """429 rather than 401: the caller should retry later, not go looking for a new token."""
    raw = token_for(person)
    headers = {"Authorization": f"Bearer {raw}"}
    client.get("/api/v1/me", headers=headers)

    assert client.get("/api/v1/me", headers=headers).status_code == 429


# ---------------------------------------------------- the endpoints a token guards


@override_settings(
    POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="a-token", POSTULO_ENDPOINT_RATE="2/h"
)
def test_metrics_is_bounded_and_says_when_to_come_back(client):
    headers = {"Authorization": "Bearer a-token"}
    for _ in range(2):
        assert client.get(reverse("core:metrics"), headers=headers).status_code == 200

    refused = client.get(reverse("core:metrics"), headers=headers)

    assert refused.status_code == 429
    assert int(refused["Retry-After"]) > 0


@override_settings(
    POSTULO_LOGS_ENDPOINT_ENABLED=True, POSTULO_LOGS_TOKEN="a-token", POSTULO_ENDPOINT_RATE="2/h"
)
def test_the_log_endpoint_is_bounded_too(client):
    headers = {"Authorization": "Bearer a-token"}
    for _ in range(2):
        assert client.get(reverse("core:logs_endpoint"), headers=headers).status_code == 200

    refused = client.get(reverse("core:logs_endpoint"), headers=headers)

    assert refused.status_code == 429
    assert int(refused["Retry-After"]) > 0


@override_settings(
    POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="a-token", POSTULO_ENDPOINT_RATE="1/h"
)
def test_a_wrong_token_is_still_refused_before_anything_is_counted(client):
    """The limit protects the work, and there is no work to protect behind a bad token."""
    for _ in range(5):
        assert client.get(reverse("core:metrics")).status_code == 401

    allowed = client.get(reverse("core:metrics"), headers={"Authorization": "Bearer a-token"})

    assert allowed.status_code == 200


# ------------------------------------------------------------------- the defaults


def test_the_shipped_defaults_are_set_and_readable(settings):
    """Not the numbers, which an operator may change: that each one parses to a real limit."""
    for name in ("POSTULO_CAPTURE_RATE", "POSTULO_API_RATE", "POSTULO_ENDPOINT_RATE"):
        assert throttle.rate_for(name), f"{name} does not parse to a limit"


def test_capture_is_the_tightest_of_them(settings):
    """It is the only one that makes this server talk to somebody else's."""
    per_hour = {
        name: throttle.rate_for(name).times * 3600 / throttle.rate_for(name).seconds
        for name in ("POSTULO_CAPTURE_RATE", "POSTULO_API_RATE", "POSTULO_ENDPOINT_RATE")
    }

    assert per_hour["POSTULO_CAPTURE_RATE"] == min(per_hour.values())


def test_the_clock_is_the_real_one():
    """Guards the monkeypatched test above from passing against a stub left behind."""
    assert abs(throttle._now() - time.time()) < 5
