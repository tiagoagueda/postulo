"""Metrics at /metrics: off unless asked for, and carrying nothing about anybody.

In `tests/security/` because the question that matters is what leaves the instance. The
answer is meant to be "counts, and nothing else": how many applications exist, not whose;
how many copies are waiting, not for what. A metric with somebody's identifier in a label
is a record of what they are doing, exported somewhere else, and calling it monitoring does
not change that.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TOKEN = "a-long-random-monitoring-token"


@pytest.fixture
def on(settings):
    settings.POSTULO_METRICS_ENABLED = True
    settings.POSTULO_METRICS_TOKEN = ""
    return settings


def fetch(client, token: str = ""):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.get(reverse("core:metrics"), **headers)


# --------------------------------------------------------------------- off


def test_off_by_default_and_saying_nothing(client, settings):
    """A 404, not a 403: a refusal would confirm that something is there."""
    assert settings.POSTULO_METRICS_ENABLED is False
    assert fetch(client).status_code == 404


def test_being_off_holds_even_with_a_token(client, settings):
    settings.POSTULO_METRICS_TOKEN = TOKEN
    assert fetch(client, TOKEN).status_code == 404


# ---------------------------------------------------------------- the token


def test_with_a_token_set_nothing_else_gets_in(client, on):
    on.POSTULO_METRICS_TOKEN = TOKEN

    assert fetch(client).status_code == 401
    assert fetch(client, "not-the-token").status_code == 401
    assert fetch(client, TOKEN).status_code == 200


def test_without_a_token_it_serves_because_there_is_nothing_secret_in_it(client, on):
    """Unlike the log endpoint, which refuses. These are counts, not records.

    The administration page says plainly that anybody who can reach the instance can read
    them, so the choice is made with the facts in view rather than by a surprise.
    """
    response = fetch(client)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")


# ------------------------------------------------------------- what it says


def test_it_is_the_format_prometheus_reads(client, on):
    body = fetch(client).content.decode()

    assert "# HELP postulo_info" in body
    assert "# TYPE postulo_info gauge" in body
    assert re.search(r'postulo_info\{[^}]*version="[^"]+"[^}]*\} 1', body)
    assert body.endswith("\n"), "the exposition format ends with one"


def test_the_numbers_an_operator_actually_watches(client, on, user):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture")
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)

    body = fetch(client).content.decode()

    assert 'postulo_records{kind="applications"} 1' in body
    assert 'postulo_records{kind="companies"} 1' in body
    assert 'postulo_records{kind="people"} 1' in body
    assert 'postulo_pending{kind="document_copies"} 0' in body
    assert "postulo_database_reachable 1" in body
    assert "postulo_migrations_applied 1" in body


def test_nothing_in_here_names_anybody(client, on, user):
    """The whole point. Counts, never who.

    A company called Aperture, a person called applicant, an application for a role called
    Engineer: none of those words may appear anywhere in the output.
    """
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, Contact, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Test Engineer", url="https://example.org/jobs/1"
    )
    Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)

    body = fetch(client).content.decode().lower()

    for secret in (
        "aperture",
        "cave",
        "johnson",
        "test engineer",
        "example.org",
        user.email.lower(),
        user.username.lower(),
    ):
        assert secret not in body, f"{secret!r} leaked into the metrics"


def test_a_label_never_carries_an_address(client, on):
    """A URL as a label is a record of what somebody looked at, under another name."""
    body = fetch(client).content.decode()

    labels = re.findall(r"\{([^}]*)\}", body)
    for group in labels:
        assert "/" not in group.replace("\\/", ""), f"a path appeared in a label: {group}"
        assert "http" not in group


def test_the_answer_is_never_cached(client, on):
    assert "no-store" in fetch(client)["Cache-Control"]


def test_the_administration_page_says_whether_they_are_open(
    client, on, admin_user, tmp_path, settings
):
    """An operator should learn this from the page, not from a port scan."""
    settings.POSTULO_LOG_DIR = str(tmp_path)
    client.force_login(admin_user)

    html = client.get(reverse("server:logs")).content.decode()
    assert 'data-metrics-endpoint="on"' in html
    assert "anybody who can reach this instance can read them" in html

    on.POSTULO_METRICS_TOKEN = TOKEN
    html = client.get(reverse("server:logs")).content.decode()
    assert "POSTULO_METRICS_TOKEN" in html

    on.POSTULO_METRICS_ENABLED = False
    html = client.get(reverse("server:logs")).content.decode()
    assert "data-metrics-endpoint" not in html, "off: nothing to say"
