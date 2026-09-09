"""Reaching somebody on a telephone, when the instance has something that can.

Postulo ships nothing that sends a text message and this module sends nothing by itself. What
it is, is the one place a text message goes through, so that the limits, the refusals and the
record of what happened exist before any gateway does.

**Why this is a transport and not a notifier.** A notifier's credentials belong to the person
— their Twilio account, their Apprise endpoint — and a person locked out of their account is
exactly the one whose own gateway may be unreachable. Worse, "the account holder configured
the channel that proves they are the account holder" is circular. A channel carrying a way
back in has to be operated by the *instance*, which is what a transport already is (#143).

**Why no gateway ships.** Every one of them is somebody else's jurisdiction. An SMS route
means a telephone number, a message and a timestamp reaching Twilio, Vonage or a national
aggregator on every send — from an application whose whole argument is that a self-hoster's
data answers to them. That is a real cost, it is a reason some operators will refuse it
outright, and it is not Postulo's to impose by shipping a default. The kind exists; the
package is somebody else's, exactly as it is for mail over an HTTP API.

**And SMS is deliberately not the first way back into an account.** #103 lists three
candidates and this is the one that needs a third party, costs money per message, and is
defeated by a SIM swap — which is not exotic. An administrator issuing a recovery link needs
no third party at all and answers the same question for the self-hosted instance with one
administrator, which is most of them. This exists because a *confirmation* code has to reach
a handset before a number can be trusted at all (#142); being a recovery route is something
it may earn afterwards, not the reason it was built.
"""

from __future__ import annotations

import logging

from django.utils.translation import gettext as _

from postulo.plugins import base

logger = logging.getLogger(__name__)

#: How many text messages one account may cause in an hour, and how many may go to one
#: number. Two limits because they bound different mistakes: the first is somebody driving
#: the resend button, the second is a stranger whose number was mistyped into a form and who
#: has no way to make it stop. Mail that fails costs nothing; this costs money and annoys
#: somebody who never asked to be involved.
PER_ACCOUNT_SETTING = "POSTULO_TEXT_RATE"
PER_NUMBER_SETTING = "POSTULO_TEXT_PER_NUMBER_RATE"


class NoGateway(Exception):
    """Nothing on this instance can reach a telephone."""


class TooMany(Exception):
    """The allowance is spent. Carries the wait so a caller can say how long."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(str(_("Too many messages. Try again shortly.")))


def gateway():
    """The transport that carries text messages, or ``None``."""
    from . import transport

    return transport.selected(base.TEXT)


def can_reach_a_number() -> bool:
    """Whether this instance has any way to send a text message at all."""
    return gateway() is not None


def send(number: str, body: str, *, on_behalf_of=None) -> bool:
    """Send one message, or say why not. Never raises for a gateway's own failure.

    `number` is in international form, because a gateway in another country cannot dial
    anything else, and the shape was checked by the channel that produced it (#146).
    """
    from postulo.core import throttle

    from . import transport

    carrier = gateway()
    if carrier is None:
        raise NoGateway(str(_("This instance has no way to send a text message.")))

    _spend(throttle, PER_ACCOUNT_SETTING, "text-account", on_behalf_of)
    _spend(throttle, PER_NUMBER_SETTING, "text-number", number)

    message = base.TextMessage(to=number, body=body)
    try:
        sent = carrier.deliver([message], transport.configuration(carrier)) or 0
    except Exception:
        # Logged without the number or the body: this line ends up in an operator's log and
        # the body is a code that gets somebody into an account.
        logger.exception("Text transport %r failed to deliver", carrier.name)
        return False
    return bool(sent)


def _spend(throttle, setting: str, action: str, who) -> None:
    """One allowance, translated into this module's own refusal.

    `who` may be `None` — a confirmation sent while nobody is signed in, which is the case a
    recovery flow is. The per-number limit still applies and is the one that matters there.
    """
    if who is None:
        return
    try:
        throttle.consume(action, who, throttle.rate_for(setting))
    except throttle.TooOften as too_often:
        raise TooMany(too_often.retry_after) from too_often
