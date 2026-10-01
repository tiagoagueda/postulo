"""The image's own health check must not depend on the operator's host list.

The container's `HEALTHCHECK` is

    curl -fsS http://127.0.0.1:8000/healthz

which sends `Host: 127.0.0.1:8000`. `CommonMiddleware` validates the host before any view
runs, so on an instance whose `POSTULO_ALLOWED_HOSTS` named only its public host -- which
is what the install pages tell an operator to write -- every probe was refused with a 400.
Docker marked a container that was serving pages unhealthy, and the scheduler and worker,
which wait for a healthy web container, never started (#580).

The probe is answered before the host is looked at, and these hold that to its exact size:
the one address, asked from inside the container, and nothing else. Everybody else still
needs a host the instance answers to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

PROBE = "postulo.core.middleware.HealthProbeMiddleware"
#: The header `curl http://127.0.0.1:8000/healthz` sends.
AS_THE_PROBE = {"HTTP_HOST": "127.0.0.1:8000"}


@pytest.fixture(autouse=True)
def only_the_public_host(settings):
    """What the install pages show: the instance's own name and nothing else."""
    settings.ALLOWED_HOSTS = ["postulo.example.org"]


def test_the_probe_is_answered_whatever_the_host_list_holds(client):
    response = client.get("/healthz", **AS_THE_PROBE)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"], "and it is still a request like any other"


def test_the_probe_can_still_say_the_database_is_gone(client, monkeypatch):
    """Answering early must not turn the probe into one that cannot fail (#82)."""
    from django.db import connection

    def refuse():
        raise RuntimeError("the database has gone away")

    monkeypatch.setattr(connection, "ensure_connection", refuse)

    response = client.get("/healthz", **AS_THE_PROBE)

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_a_head_request_is_a_probe_too(client):
    assert client.head("/healthz", **AS_THE_PROBE).status_code == 200


def test_somebody_elsewhere_still_needs_a_host_the_instance_answers_to(client):
    """Only the container asking itself. A stranger with a wrong host is refused as before."""
    response = client.get("/healthz", REMOTE_ADDR="203.0.113.5", **AS_THE_PROBE)

    assert response.status_code == 400


@pytest.mark.parametrize(
    "forwarded_for",
    [
        "203.0.113.5",
        # Nothing usable in the header leaves the peer address standing, which is the
        # loopback one: the request was forwarded all the same.
        "unknown",
    ],
)
def test_a_request_a_proxy_passed_on_is_not_the_probe(client, forwarded_for):
    """A proxy on the same host is a loopback peer too, and what it carries is the world's."""
    response = client.get("/healthz", HTTP_X_FORWARDED_FOR=forwarded_for, **AS_THE_PROBE)

    assert response.status_code == 400


@pytest.mark.parametrize(
    "path", ["/", "/healthz/", "/healthz/x", "/metrics", "/logs", "/accounts/login/"]
)
def test_nothing_but_the_health_check_is_let_past_the_host_list(client, path):
    response = client.get(path, **AS_THE_PROBE)

    assert response.status_code == 400, f"{path} was answered for a host nobody allowed"


def test_only_a_read_is_a_probe(client):
    assert client.post("/healthz", **AS_THE_PROBE).status_code == 400


def test_the_right_host_still_works_from_anywhere(client):
    """The uptime monitor outside, asking by the instance's name, takes the ordinary path."""
    response = client.get("/healthz", REMOTE_ADDR="203.0.113.5", HTTP_HOST="postulo.example.org")

    assert response.status_code == 200


def test_the_probe_does_not_stand_in_for_the_redirect_exemption(client, settings):
    """It sits behind `SecurityMiddleware`, so the exemption in `prod.py` is still what
    lets plain HTTP through. Answering any earlier would make `test_health_redirect.py`
    pass with that exemption deleted."""
    settings.SECURE_SSL_REDIRECT = True
    settings.SECURE_REDIRECT_EXEMPT = []

    # By the instance's own name: the redirect is built from the host, and has to be
    # allowed one to be built at all.
    response = client.get("/healthz", HTTP_HOST="postulo.example.org")

    assert response.status_code == 301


def test_it_sits_directly_in_front_of_the_host_check(settings):
    chain = settings.MIDDLEWARE
    assert chain.index(PROBE) == chain.index("django.middleware.common.CommonMiddleware") - 1


def test_the_address_it_knows_is_the_one_the_view_is_mounted_at():
    assert reverse("core:healthz") == "/healthz"


def test_the_dockerfile_still_probes_from_inside_the_container():
    """The exception is for a loopback peer, so the probe has to stay one."""
    dockerfile = (Path(__file__).resolve().parents[2] / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    probe = next(line for line in dockerfile.splitlines() if "/healthz" in line and "CMD" in line)

    assert "http://127.0.0.1:" in probe, probe


def test_the_image_check_runs_it_the_way_that_used_to_fail():
    """`check-image.sh` allowed the very host the probe uses, so it could not see this.

    Its second run names only a public host and waits for Docker's own verdict. Read
    rather than run: building an image needs a daemon the test job does not have.
    """
    script = (Path(__file__).resolve().parents[2] / "scripts" / "check-image.sh").read_text(
        encoding="utf-8"
    )

    assert "POSTULO_ALLOWED_HOSTS=postulo.example.org" in script
    assert ".State.Health.Status" in script
