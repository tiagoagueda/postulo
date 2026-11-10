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
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

pytestmark = pytest.mark.django_db

TOKEN = "a-long-random-monitoring-token"


@pytest.fixture
def on(settings):
    settings.POSTULO_METRICS_ENABLED = True
    settings.POSTULO_METRICS_TOKEN = TOKEN
    return settings


def fetch(client, token: str | None = TOKEN):
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
    assert fetch(client, "").status_code == 401
    assert fetch(client, "not-the-token").status_code == 401
    assert fetch(client, TOKEN).status_code == 200


def test_a_token_with_a_byte_above_ascii_is_refused_not_a_crash(client, on):
    """Headers arrive as Latin-1; `compare_digest` on such a str raised a 500 (#372)."""
    on.POSTULO_METRICS_TOKEN = TOKEN

    response = client.get(reverse("core:metrics"), HTTP_AUTHORIZATION="Bearer café")

    assert response.status_code == 401


def test_without_a_token_it_refuses_rather_than_serve_anybody(client, on):
    """Unlike what this once did (#801): the numbers name the exact versions and the number of
    accounts, which is not for whoever can reach the instance."""
    on.POSTULO_METRICS_TOKEN = ""

    response = fetch(client, "")

    assert response.status_code == 503
    assert b"postulo_" not in response.content


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


# --------------------------------------------------- what a scrape costs (#1113)


@pytest.fixture
def graphs(monkeypatch):
    """Each migration graph built from the migration files while the test runs."""
    from django.db.migrations.loader import MigrationLoader

    built = []
    real = MigrationLoader.build_graph

    def counting(self):
        built.append(self)
        return real(self)

    monkeypatch.setattr(MigrationLoader, "build_graph", counting)
    return built


def _leaf_migration() -> tuple[str, str]:
    """One migration nothing depends on, so it can be marked unapplied and applied again."""
    from django.db.migrations.loader import MigrationLoader

    return sorted(MigrationLoader(connection).graph.leaf_nodes())[0]


def test_the_migrations_are_read_once_and_not_at_every_scrape(client, on, graphs):
    """Every scrape read every migration file of every app to say the same 1 (#1113).

    New migrations arrive only with new code, and new code is a new process, so a process
    that has seen them all applied has its answer: the second scrape builds no graph and
    does not read the table of applied migrations.
    """
    assert "postulo_migrations_applied 1" in fetch(client).content.decode()
    assert len(graphs) == 1

    with CaptureQueriesContext(connection) as queries:
        body = fetch(client).content.decode()

    assert "postulo_migrations_applied 1" in body
    assert len(graphs) == 1, "the second scrape built the migration graph again"
    assert not [query["sql"] for query in queries if "django_migrations" in query["sql"]]


def test_an_instance_behind_on_its_migrations_is_asked_again_at_every_scrape(client, on):
    """Only a yes is remembered (#1113).

    A process that started before `migrate` had finished says 0, and has to say 1 at the
    first scrape after it has: a no kept for the life of the process would be an alert
    that never clears.
    """
    from django.db.migrations.recorder import MigrationRecorder

    leaf = _leaf_migration()
    recorder = MigrationRecorder(connection)
    recorder.record_unapplied(*leaf)

    assert "postulo_migrations_applied 0" in fetch(client).content.decode()
    assert "postulo_migrations_applied 0" in fetch(client).content.decode()

    recorder.record_applied(*leaf)

    assert "postulo_migrations_applied 1" in fetch(client).content.decode()


def test_a_question_that_could_not_be_asked_is_asked_again(client, on, monkeypatch):
    """A check that raised says 0, and is not what the process remembers (#1113)."""
    from django.db.migrations.executor import MigrationExecutor

    def broken(self, targets, clean_start=False):
        raise RuntimeError("the migration table could not be read")

    with monkeypatch.context() as patched:
        patched.setattr(MigrationExecutor, "migration_plan", broken)
        assert "postulo_migrations_applied 0" in fetch(client).content.decode()

    assert "postulo_migrations_applied 1" in fetch(client).content.decode()


def test_a_new_process_asks_about_its_own_migrations(client, on, graphs):
    """What one process remembered is nothing to the next one (#1113).

    An upgrade is new code in a new process, which starts with nothing remembered: it
    reads its own migration files and says 0 until they are applied. `forget_migrations`
    stands in for the new process here, as it does between two tests.
    """
    from django.db.migrations.recorder import MigrationRecorder

    from postulo.core import metrics

    leaf = _leaf_migration()
    assert "postulo_migrations_applied 1" in fetch(client).content.decode()

    metrics.forget_migrations()
    MigrationRecorder(connection).record_unapplied(*leaf)
    built = len(graphs)

    assert "postulo_migrations_applied 0" in fetch(client).content.decode()
    assert len(graphs) == built + 1


def test_the_administration_page_says_whether_they_are_open(
    client, on, admin_user, tmp_path, settings
):
    """An operator should learn this from the page, not from a port scan."""
    settings.POSTULO_LOG_DIR = str(tmp_path)
    client.force_login(admin_user)

    html = client.get(reverse("server:logs")).content.decode()
    assert 'data-metrics-endpoint="on"' in html
    assert "behind the token in" in html

    on.POSTULO_METRICS_TOKEN = ""
    html = client.get(reverse("server:logs")).content.decode()
    assert "refuses to answer" in html

    on.POSTULO_METRICS_TOKEN = TOKEN
    html = client.get(reverse("server:logs")).content.decode()
    assert "POSTULO_METRICS_TOKEN" in html

    on.POSTULO_METRICS_ENABLED = False
    html = client.get(reverse("server:logs")).content.decode()
    assert "data-metrics-endpoint" not in html, "off: nothing to say"
