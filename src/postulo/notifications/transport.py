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

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

#: What is used when nobody has chosen, and what an instance that has never heard of
#: transports goes on using.
DEFAULT_TRANSPORT = "smtp"


def available() -> list:
    """Every transport this instance could use."""
    from postulo.plugins.registry import plugins

    return plugins("transport")


def selected():
    """The transport that carries the mail, or ``None`` if there is not one.

    An administrator's choice, else the built-in SMTP one, else whatever single transport
    is installed. Never "the first third-party one wins": the registry prefers third-party
    plugins for sources because a plugin written for one job board knows more about it than
    a general parser does, and that argument does not transfer to *where this instance's
    mail goes*. Installing a package must not silently redirect the mail.
    """
    installed = available()
    if not installed:
        return None
    chosen = _chosen_name()
    for transport in installed:
        if transport.name == chosen:
            return transport
    for transport in installed:
        if transport.name == DEFAULT_TRANSPORT:
            return transport
    return installed[0] if len(installed) == 1 else None


def _chosen_name() -> str:
    from postulo.core import site

    try:
        return site.current().email_transport or DEFAULT_TRANSPORT
    except Exception:
        return DEFAULT_TRANSPORT


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
    return {**(row.transport_config or {}), **row.transport_secrets}


class PluggableBackend(BaseEmailBackend):
    """The one backend named in ``MAILERS``. It carries nothing and chooses at send time.

    Nothing is resolved in ``__init__`` beyond what a send needs, because Django builds one
    of these for every message and caches none — which is exactly what makes a settings page
    possible without a restart.
    """

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
        try:
            return transport.deliver(list(email_messages), config) or 0
        except Exception:
            logger.exception("Mail transport %r failed to deliver", transport.name)
            if not self.fail_silently:
                raise
            return 0

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


def recovery_routes(*, without: str = "") -> list[str]:
    """Ways somebody locked out of their own account could get back into it.

    Today there is one that this instance operates — email — and one that belongs to the
    person: a passkey, which signs them in without the password they have forgotten. A TOTP
    recovery code is deliberately not on this list: it is a *second* factor, so it helps
    somebody who still knows their password and does nothing for somebody who does not.

    When another route lands (#103 — SMS, Apprise, an administrator-issued link) it is added
    here and the lock below opens by itself. That is the point of writing it as a list.
    """
    routes = []
    transport = selected()
    if transport is not None and transport.name != without:
        routes.append("email")
    if not _accounts_needing_email():
        routes.append("passkey")
    return routes


def _accounts_needing_email() -> int:
    """Active accounts with nothing but email to get back in with.

    Counted rather than assumed. On a typical instance this is every account and the lock is
    shut, which is the honest answer; on one where every person holds a passkey it is zero
    and the lock opens, which is the reason not to hardcode the answer.
    """
    from allauth.mfa.models import Authenticator
    from django.contrib.auth import get_user_model

    with_a_passkey = Authenticator.objects.filter(type=Authenticator.Type.WEBAUTHN).values_list(
        "user_id", flat=True
    )
    return get_user_model().objects.filter(is_active=True).exclude(pk__in=with_a_passkey).count()


def refuse_switching_off(name: str) -> str:
    """Why this transport may not be switched off, or an empty string if it may.

    Named after what it protects, the way *Server settings → People* refuses to remove the
    last administrator rather than saying "not allowed".
    """
    transport = selected()
    if transport is None or transport.name != name:
        return ""
    if recovery_routes(without=name):
        return ""
    stranded = _accounts_needing_email()
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
