"""Whether mail actually delivers, and what that changes about the lock (#152).

`recovery_routes()` decides whether anybody could get back into their account, and the mail
transport's lock rests on it. It used to check whether a transport was *selected*, which is a
statement about configuration and not about delivery: an instance whose relay had been
switched off still counted email as the last way in, and refused to let the transport go on
the strength of a route that delivered nothing.

Three things are worth being careful about, and each has tests here.

**Nothing may send mail to answer the question.** The lock is evaluated while rendering a
page, so the answer comes from what the last send recorded, never from a probe.

**Unknown counts as working, and so does a short run of failures.** A lock that opens on a
shrug is worse than one that stays shut on an optimistic guess, and a relay that refuses one
address has told us about that address rather than about itself.

**Opening the lock is not the interesting part.** When mail is the last route and it is
failing, those accounts are already stranded; the page saying so is the useful thing.
"""

from __future__ import annotations

import contextlib

import pytest
from django.core.mail import EmailMessage
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from postulo.core import site
from postulo.core.models import SiteSettings
from postulo.notifications import transport
from postulo.notifications.smtp import SMTPTransport
from postulo.notifications.transport import PluggableBackend
from postulo.plugins import registry
from postulo.plugins.base import FieldSpec
from postulo.plugins.base import TestResult as PluginTestResult

pytestmark = pytest.mark.django_db

BROKEN = SiteSettings.MAIL_FAILURES_BEFORE_BROKEN


def raising(error: Exception):
    """A callable that raises, for monkeypatching something that must not be reached."""

    def fail(*args, **kwargs):
        raise error

    return fail


class Unreliable:
    """A transport that does whatever the test tells it to.

    Configured through class attributes rather than an instance, because the registry takes
    a class and builds its own instance.
    """

    name = "smtp"  # stands in for the selected one, so the lock is asked about it
    version = "0.0.1"
    kind = "transport"
    label = "SMTP"
    description = "Fails on request."
    raises: Exception | None = None
    delivers: int | None = None

    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def deliver(self, messages: list, config: dict) -> int:
        if self.raises is not None:
            raise self.raises
        return len(messages) if self.delivers is None else self.delivers


def unreliable(*, raises: Exception | None = None, delivers: int | None = None):
    return type("Unreliable", (Unreliable,), {"raises": raises, "delivers": delivers})


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )


@contextlib.contextmanager
def carrying(plugin):
    """`plugin` carries the mail for the duration, in place of SMTP."""
    registry.unregister_builtin("transport", SMTPTransport)
    registry.register_builtin("transport", plugin)
    try:
        yield
    finally:
        registry.unregister_builtin("transport", plugin)
        registry.register_builtin("transport", SMTPTransport)


def send_through(plugin) -> None:
    """One message through the pluggable backend, with `plugin` carrying it."""
    with carrying(plugin):
        backend = PluggableBackend(fail_silently=True)
        backend.send_messages([EmailMessage("s", "b", "from@example.org", ["to@example.org"])])


def fail_times(count: int) -> None:
    for _ in range(count):
        send_through(unreliable(raises=OSError("relay refused the connection")))


# ------------------------------------------------------------ what a send records


def test_a_send_that_works_is_remembered(db):
    send_through(unreliable())

    health = site.mail_health()
    assert health["delivers"] and health["last_ok_at"] is not None
    assert health["failures"] == 0


def test_a_send_that_raises_is_remembered_with_what_it_said(db):
    send_through(unreliable(raises=OSError("relay refused the connection")))

    health = site.mail_health()
    assert health["failures"] == 1
    assert "relay refused the connection" in health["last_error"]
    assert health["last_error_at"] is not None


def test_a_transport_that_accepts_nothing_is_a_failure_too(db):
    """Returning zero is not an error and is not a delivery either."""
    send_through(unreliable(delivers=0))

    assert site.mail_health()["failures"] == 1


def test_a_success_clears_the_run_of_failures(db):
    fail_times(BROKEN)
    assert not site.mail_delivers()

    send_through(unreliable())

    assert site.mail_delivers()
    assert site.mail_health()["last_error"] == ""


def test_a_send_is_not_a_row_update_every_time(db):
    """A write per message would be a lot of writes for a fact read to the hour."""
    send_through(unreliable())
    first = SiteSettings.get().updated_at

    send_through(unreliable())

    assert SiteSettings.get().updated_at == first


def test_bookkeeping_never_breaks_a_send(db, monkeypatch):
    monkeypatch.setattr(SiteSettings, "record_mail", raising(RuntimeError("no")))

    send_through(unreliable())  # the message went out; the note about it did not


# -------------------------------------------------------------- unknown is working


def test_an_instance_that_has_never_sent_anything_counts_as_working(db):
    assert site.mail_health()["ever_tried"] is False
    assert site.mail_delivers() is True


def test_one_failure_is_not_a_broken_relay(db, admin):
    """A refused address says something about that address, not about the transport."""
    send_through(unreliable(raises=OSError("550 no such user")))

    assert site.mail_delivers() is True
    assert transport.refuse_switching_off("smtp"), "still the last way in, still locked"


def test_it_takes_a_run_of_them(db):
    fail_times(BROKEN - 1)
    assert site.mail_delivers() is True

    fail_times(1)

    assert site.mail_delivers() is False


def test_a_broken_settings_row_answers_that_mail_works(db, monkeypatch):
    """Failing the other way would let a database problem unlock people's accounts."""
    monkeypatch.setattr(site, "current", raising(RuntimeError("no settings")))

    assert site.mail_delivers() is True


def test_nothing_is_sent_to_answer_the_question(db, mailoutbox):
    fail_times(1)
    before = len(mailoutbox)

    site.mail_delivers()
    transport.recovery_routes()
    transport.refuse_switching_off("smtp")

    assert len(mailoutbox) == before, "evaluating a lock must have no side effect"


# ----------------------------------------------------------------- the route list


def test_a_route_separates_existing_from_delivering(db):
    fail_times(BROKEN)

    email = next(r for r in transport.all_routes() if r.name == "email")

    assert email.exists is True, "a transport is still selected"
    assert email.delivers is False
    assert email.counts is False
    assert email.trouble, "and it says why, for the page"


def test_a_route_that_cannot_deliver_is_not_counted(db, admin):
    assert "email" in transport.recovery_routes()

    fail_times(BROKEN)

    assert "email" not in transport.recovery_routes()


def test_the_lock_opens_when_the_route_it_protects_delivers_nothing(db, admin):
    """Those accounts are stranded either way; refusing does not unstrand them."""
    assert transport.refuse_switching_off("smtp")

    fail_times(BROKEN)

    assert transport.refuse_switching_off("smtp") == ""


def test_a_working_transport_is_still_locked(db, admin):
    send_through(unreliable())

    assert transport.refuse_switching_off("smtp")


# --------------------------------------------------------------------- the page


def test_the_page_says_mail_has_been_failing(client, admin):
    fail_times(BROKEN)
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "data-mail-failing" in html
    assert "relay refused the connection" in html, "what it said, not just that it failed"


def test_the_page_says_how_many_people_that_strands(client, admin, django_user_model):
    django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )
    fail_times(BROKEN)
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "2 people" in html, "the useful fact, not that a lock changed state"


def test_the_page_says_when_mail_last_went_out(client, admin):
    send_through(unreliable())
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "data-mail-ok" in html
    assert "data-mail-failing" not in html


def test_a_quiet_instance_says_neither(client, admin):
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "data-mail-failing" not in html and "data-mail-ok" not in html


@override_settings(
    MAILERS={"default": {"BACKEND": "postulo.notifications.transport.PluggableBackend"}}
)
def test_the_test_button_records_what_happened(client, admin):
    """One recording point: proving the configuration and using it are the same evidence.

    The test suite normally sends into `locmem`, so the pluggable backend is put back for
    this one — the claim being checked is precisely that pressing the button goes through
    the same code a real send does, and nothing extra had to be wired up for it.
    """
    client.force_login(admin)

    with carrying(unreliable()):
        client.post(reverse("server:email_test"), {"to": "somebody@example.org"})

    assert site.mail_health()["last_ok_at"] is not None


@override_settings(
    MAILERS={"default": {"BACKEND": "postulo.notifications.transport.PluggableBackend"}}
)
def test_a_failing_test_button_records_that_too(client, admin):
    client.force_login(admin)

    with carrying(unreliable(raises=OSError("authentication failed"))):
        client.post(reverse("server:email_test"), {"to": "somebody@example.org"})

    assert "authentication failed" in site.mail_health()["last_error"]


def test_a_stale_success_is_written_again(db):
    send_through(unreliable())
    old = timezone.now() - SiteSettings.MAIL_OK_INTERVAL * 2
    SiteSettings.objects.filter(pk=1).update(mail_last_ok_at=old)

    send_through(unreliable())

    assert SiteSettings.get().mail_last_ok_at > old
