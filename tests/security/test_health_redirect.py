"""The liveness probe has to be able to fail.

With `POSTULO_SSL_REDIRECT` on -- the production default -- `SecurityMiddleware` answered
`/healthz` with a 301 to `https://127.0.0.1:8000/healthz` before any view ran and before
anything touched the database. The container's health check is

    curl -fsS http://127.0.0.1:8000/healthz

and `curl -f` fails only on 4xx and 5xx, so a 301 exited 0 with an empty body and Docker
marked the container healthy. Verified against a server that does nothing but redirect:

    $ curl -fsS http://127.0.0.1:17431/healthz ; echo $?
    0

So the probe reported healthy whenever `SecurityMiddleware` was loaded, which is always --
with the database gone, the migrations unapplied, every view raising. The 503 the `healthz`
view returns was unreachable in production, and so was the restart that a failing check plus
`restart: unless-stopped` would have produced (#82).

`SECURE_REDIRECT_EXEMPT` fixes it, and these hold the fix to its exact size: the two
endpoints a machine talks to over plain HTTP inside the deployment, and nothing else. An
exemption that was too broad would be a worse bug than the one it replaced.
"""

from __future__ import annotations

import re

import pytest
from django.test import override_settings
from django.urls import reverse

#: What production says. Imported rather than retyped, so this cannot pass against a list
#: that no longer matches the one shipped.
from postulo.config.settings.prod import SECURE_REDIRECT_EXEMPT

pytestmark = pytest.mark.django_db

#: Production, as far as the redirect is concerned.
AS_DEPLOYED = override_settings(
    SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=SECURE_REDIRECT_EXEMPT
)


def test_the_health_check_reaches_the_view(client):
    """The whole point: a probe that can report something other than success."""
    with AS_DEPLOYED:
        response = client.get(reverse("core:healthz"))

    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_the_health_check_can_now_report_a_broken_database(client, monkeypatch):
    """The 503 that was unreachable in production.

    Without this the exemption would only prove that a 200 gets through, which a redirect
    also managed to look like from `curl -f`'s point of view.
    """
    from django.db import connection

    def refuse():
        raise RuntimeError("the database has gone away")

    monkeypatch.setattr(connection, "ensure_connection", refuse)

    with AS_DEPLOYED:
        response = client.get(reverse("core:healthz"))

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


@override_settings(POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="")
def test_a_scraper_inside_the_deployment_reaches_the_metrics(client):
    """A scraper reaching the container directly is on the same plain-HTTP hop as the
    health check, and the numbers it collects carry nothing about anybody."""
    with AS_DEPLOYED:
        response = client.get(reverse("core:metrics"))

    assert response.status_code == 200
    assert "postulo_" in response.content.decode()


@override_settings(POSTULO_LOGS_ENDPOINT_ENABLED=True, POSTULO_LOGS_TOKEN="a-token")
def test_the_log_endpoint_is_still_redirected(client):
    """Deliberately not exempt, and the one asymmetry worth a test of its own.

    A log entry names a connection, a company, an application. A scrape that visibly breaks
    is better than that crossing a network in clear; an operator who wants it scrapes
    through the proxy over HTTPS, or turns the redirect off as a decision.
    """
    with AS_DEPLOYED:
        response = client.get(reverse("core:logs_endpoint"))

    assert response.status_code == 301
    assert response["Location"].startswith("https://")


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/accounts/login/",
        "/logs",
        # Near misses. `SecurityMiddleware` matches with `re.search` against the path with
        # its leading slash stripped, so an unanchored pattern would have exempted all of
        # these -- which is the worse bug this list is anchored to avoid.
        "/healthz/somewhere",
        "/server/metrics",
        "/metrics-internal",
        "/x-healthz",
        "/settings/healthz",
    ],
)
def test_everything_else_is_still_sent_to_https(client, path):
    with AS_DEPLOYED:
        response = client.get(path)

    assert response.status_code == 301, f"{path} was let through in the clear"
    assert response["Location"].startswith("https://")


def test_the_exemption_is_only_the_two_endpoints_that_need_it():
    """Stated as a list rather than inferred from behaviour, so adding a third is a
    deliberate edit to a test rather than something that slips in."""
    assert SECURE_REDIRECT_EXEMPT == [r"^healthz$", r"^metrics$"]
    for pattern in SECURE_REDIRECT_EXEMPT:
        assert pattern.startswith("^") and pattern.endswith("$"), pattern


def test_the_dockerfile_still_probes_the_path_that_is_exempt():
    """The exemption and the health check name the same path, or neither is worth much.

    A rename on either side breaks the pair silently: the probe would go on exiting 0
    against a redirect, which is precisely the failure this issue was about.
    """
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[2] / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    probe = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD ([^\n]+)", dockerfile)
    assert probe, "no HEALTHCHECK in the Dockerfile"

    command = probe.group(1)
    assert "/healthz" in command, command
    exempt = [p.strip("^$") for p in SECURE_REDIRECT_EXEMPT]
    assert any(f"/{name}" in command for name in exempt), (
        f"the health check probes something no longer exempt: {command}"
    )
