"""Which transport carries the mail, how it is configured, and when it may not be removed.

**The ordering problem this exists to solve.** A plugin cannot supply ``MAILERS``. Django
reads that when the settings module is imported; entry points are not loaded until the app
registry is ready, which is later. So "SMTP is a plugin" cannot mean "the plugin defines the
mail settings". What it can mean, and what this is: core names one backend, and that backend
asks which transport is selected and how it is configured, at send time.

That is the same mechanism the Email page needed anyway (#84) — configuration read from the
database rather than frozen at import — so the two are one piece of work rather than two.

**The interlock.** A transport may not be switched off while it is the last way anybody
could get back into their account. That is written below as a rule that is *evaluated*, not
as ``if plugin == "smtp": refuse``. Two reasons. A hardcoded exception is one nobody
deletes, so the day another recovery route lands (#103) the lock would stay shut out of
inertia. And the rule is the more useful thing regardless: an instance that later runs a
second transport should be able to switch this one off, and a name check would refuse that
too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.translation import gettext as _

from postulo.plugins import base

logger = logging.getLogger(__name__)

#: What is used when nobody has chosen, and what an instance that has never heard of
#: transports goes on using.
DEFAULT_TRANSPORT = "smtp"


def available(medium: str = base.MAIL) -> list:
    """Every transport this instance could use to carry ``medium``."""
    from postulo.plugins.registry import plugins

    return [item for item in plugins("transport") if base.medium_of(item) == medium]


def selected(medium: str = base.MAIL):
    """The transport that carries ``medium``, or ``None`` if there is not one.

    An administrator's choice, else the built-in one for that medium if there is one, else
    whatever single transport carries it. Never "the first third-party one wins": the
    registry prefers third-party plugins for sources because a plugin written for one job
    board knows more about it than a general parser does, and that argument does not
    transfer to *where this instance's messages go*. Installing a package must not silently
    redirect them.

    Postulo ships a mail transport and no text one, so an instance with nothing installed
    answers ``None`` here for text — which is the honest answer and the reason a telephone
    number cannot yet be confirmed (#143, #146).
    """
    installed = available(medium)
    if not installed:
        return None
    chosen = _chosen_name(medium)
    for transport in installed:
        if transport.name == chosen:
            return transport
    for transport in installed:
        if transport.name == DEFAULT_TRANSPORT:
            return transport
    return installed[0] if len(installed) == 1 else None


def _chosen_name(medium: str = base.MAIL) -> str:
    from postulo.core import site

    try:
        row = site.current()
    except Exception:
        return DEFAULT_TRANSPORT if medium == base.MAIL else ""
    if medium == base.MAIL:
        return row.email_transport or DEFAULT_TRANSPORT
    return row.text_transport or ""


def configuration(transport) -> dict:
    """The settings that transport should use.

    SMTP is the exception it has to be: its settings are the named columns on the policy
    row, each overridden individually by its own environment variable, and a fresh instance
    with an empty database has to be able to send a verification email before there is a row
    to read at all. Everything else gets the generic pair, the same shape a connection uses.
    """
    from postulo.core import site

    if transport is None:
        return {}
    if transport.name == DEFAULT_TRANSPORT:
        resolved = dict(site.email_settings())
        resolved.pop("from_address", None)
        return resolved
    try:
        row = site.current()
    except Exception:
        return {}
    # One blob per medium rather than one shared blob, because two transports are selected
    # at once now and a single column could only hold one of their configurations (#143).
    if base.medium_of(transport) == base.TEXT:
        return {**(row.text_config or {}), **row.text_secrets}
    return {**(row.transport_config or {}), **row.transport_secrets}


class PluggableBackend(BaseEmailBackend):
    """The one backend named in ``MAILERS``. It carries nothing and chooses at send time.

    Nothing is resolved in ``__init__`` beyond what a send needs, because Django builds one
    of these for every message and caches none — which is exactly what makes a settings page
    possible without a restart.
    """

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        """Own ``fail_silently`` rather than inherit it.

        Django 7.0 removes it from ``BaseEmailBackend``, and reading the inherited attribute
        already raises a deprecation warning — which this backend does on exactly the path
        that matters, the one where a send has failed. A backend that honours the flag has
        to keep it itself, which is what the deprecation says to do.
        """
        super().__init__(**kwargs)
        self.fail_silently = fail_silently

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        transport = selected()
        if transport is None:
            logger.error(
                "No mail transport is installed; %d message(s) went nowhere", len(email_messages)
            )
            if not self.fail_silently:
                raise RuntimeError("No mail transport is installed.")
            return 0

        config = configuration(transport)
        self._stamp_sender(email_messages)
        # The one place every message passes through, which is why the outcome is recorded
        # here and nowhere else: the test button on the Email page sends through this too,
        # so proving the configuration and using it are the same evidence (#152).
        try:
            sent = transport.deliver(list(email_messages), config) or 0
        except Exception as error:
            logger.exception("Mail transport %r failed to deliver", transport.name)
            self._record(False, f"{type(error).__name__}: {error}")
            if not self.fail_silently:
                raise
            return 0
        self._record(bool(sent), "" if sent else "The transport accepted nothing.")
        return sent

    @staticmethod
    def _record(ok: bool, message: str) -> None:
        from postulo.core import site

        site.record_mail(ok, message)

    @staticmethod
    def _stamp_sender(email_messages) -> None:
        """Put the instance's from-address on anything that did not choose one.

        Here because ``DEFAULT_FROM_EMAIL`` is read at send time by Django's own code and
        allauth's, and neither offers a hook. A message that names its own sender keeps it:
        this is for the ninety-nine that do not, not a rule about who Postulo may send as.
        """
        from postulo.core import site

        try:
            chosen = site.email_settings()["from_address"]
        except Exception:
            return
        for message in email_messages:
            if message.from_email == settings.DEFAULT_FROM_EMAIL:
                message.from_email = chosen


# ------------------------------------------------------- the interlock (#100, #104)


@dataclass(frozen=True)
class Route:
    """One way back into an account, and whether it currently works.

    Two questions, kept apart on purpose. *Exists* is about configuration — a transport is
    installed, a person holds a passkey. *Delivers* is about evidence, and every route has
    to answer it in its own way: mail from what the last send did, SMS from whether the
    number was ever confirmed, an administrator-issued link from whether an administrator
    is still there to issue one. Writing the pair down now is cheaper than discovering three
    times over that `recovery_routes()` was counting configuration (#152).
    """

    name: str
    exists: bool
    delivers: bool = True
    #: Why it does not deliver, for the page rather than for the decision.
    trouble: str = ""

    @property
    def counts(self) -> bool:
        return self.exists and self.delivers


def all_routes(*, without: str = "") -> list[Route]:
    """Every way back into an account this instance has, working or not.

    Today there is one that this instance operates — email — and one that belongs to the
    person: a passkey, which signs them in without the password they have forgotten. A TOTP
    recovery code is deliberately not on this list: it is a *second* factor, so it helps
    somebody who still knows their password and does nothing for somebody who does not.

    When another route lands (#103 — SMS, Apprise, an administrator-issued link) it is added
    here and the lock below opens by itself. That is the point of writing it as a list.
    """
    from postulo.core import site

    transport = selected()
    delivers = site.mail_delivers()
    return [
        Route(
            name="email",
            exists=transport is not None and transport.name != without,
            delivers=delivers,
            trouble="" if delivers else str(_("Mail has been failing.")),
        ),
        Route(name="passkey", exists=not accounts_needing_email()),
        Route(name="text", exists=_text_reaches_everybody(without)),
        Route(name="administrator", exists=_an_administrator_reaches_everybody()),
    ]


def _an_administrator_reaches_everybody() -> bool:
    """Whether an administrator could issue a link for every account (#103).

    The route that needs no third party at all, and the one that finally lets an instance
    switch mail off. It reaches everybody but the person doing the issuing, so a lone
    administrator without a passkey is the one account it does not cover.
    """
    from postulo.accounts import recovery

    return not recovery.accounts_no_administrator_can_reach()


def _text_reaches_everybody(without: str = "") -> bool:
    """Whether a text message is a way back in for *every* account, not merely for some.

    The same bar the passkey route is held to, and for the same reason: this list decides
    whether mail may be switched off, so a route that covers nine accounts out of ten would
    strand the tenth. A gateway plus a confirmed number on every active account is what it
    takes, which is a high bar and the correct one.
    """
    carrier = selected(base.TEXT)
    if carrier is None or carrier.name == without:
        return False
    from postulo.core import phone_numbers

    return not phone_numbers.accounts_without_a_recovery_number()


def recovery_routes(*, without: str = "") -> list[str]:
    """The names of the routes that actually count. See :func:`all_routes`."""
    return [route.name for route in all_routes(without=without) if route.counts]


def accounts_needing_email() -> int:
    """Active accounts with nothing but email to get back in with.

    Counted rather than assumed. On a typical instance this is every account and the lock is
    shut, which is the honest answer; on one where every person holds a passkey it is zero
    and the lock opens, which is the reason not to hardcode the answer.

    A second clause rather than a second assumption (#144): an account with a confirmed
    recovery number, on an instance that can send to one, is not an account that needs email.
    Both halves are required — a number is only a route while something can reach it.
    """
    from allauth.mfa.models import Authenticator
    from django.contrib.auth import get_user_model

    from postulo.core.models import PhoneNumber

    covered = set(
        Authenticator.objects.filter(type=Authenticator.Type.WEBAUTHN).values_list(
            "user_id", flat=True
        )
    )
    if selected(base.TEXT) is not None:
        from django.contrib.contenttypes.models import ContentType
        from django.utils import timezone

        from postulo.accounts.models import Profile

        covered |= set(
            PhoneNumber.objects.filter(
                content_type=ContentType.objects.get_for_model(Profile),
                is_recovery=True,
                verified_at__gte=timezone.now() - PhoneNumber.VERIFICATION_LASTS,
            ).values_list("owner_id", flat=True)
        )
    return get_user_model().objects.filter(is_active=True).exclude(pk__in=covered).count()


def refuse_switching_off(name: str) -> str:
    """Why this transport may not be switched off, or an empty string if it may.

    Named after what it protects, the way *Server settings → People* refuses to remove the
    last administrator rather than saying "not allowed".
    """
    transport = selected()
    if transport is None or transport.name != name:
        return ""
    from postulo.core import site

    if recovery_routes(without=name):
        return ""
    if not site.mail_delivers():
        # Mail is the last route and it is not delivering, so the lock is protecting a way
        # in that does not let anybody in. Opening it changes nothing for those accounts:
        # they are stranded now, and keeping an administrator from installing something
        # else does not unstrand them. The Email page says so in as many words, because
        # that fact is the useful one and a quietly-opened lock would not carry it (#152).
        return ""
    stranded = accounts_needing_email()
    # "%(count)d of them would" rather than "have", so one account and four read equally
    # well and the sentence needs no plural form.
    return str(
        _(
            "%(label)s is how this instance sends mail, and mail is the only way back into "
            "an account here — %(count)d of them would have no way in. Set up another "
            "transport first."
        )
        % {"label": getattr(transport, "label", name), "count": stranded}
    )


def refuse_removing_distribution(distribution: str) -> str:
    """The same rule, asked of a package rather than a plugin.

    An administrator switches off *distributions* on the plugins page, not plugins, so the
    interlock has to be able to answer for the package that provides the locked transport.
    """
    from postulo.plugins.installing import canonicalise

    transport = selected()
    if transport is None:
        return ""
    if canonicalise(_distribution_of(transport)) != canonicalise(distribution):
        return ""
    return refuse_switching_off(transport.name)


def _distribution_of(transport) -> str:
    """Which installed package a transport came from, or nothing for a built-in one."""
    from importlib.metadata import packages_distributions

    module = type(transport).__module__.split(".")[0]
    return (packages_distributions().get(module) or [""])[0]
