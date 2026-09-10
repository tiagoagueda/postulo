"""Mail a person sends as themselves, over their own server, under their own name.

> lets smtp plugin be internal split in 2 part, 1 part, server site that cannot be disable
> … and a second part, user side, that can be enable/disable by the user / admin with smtp
> settings by user and handle a disting workflow

The instance's half was already there: ``SMTPTransport``, ungoverned on purpose, settings in
*Server settings → Email*, refusing to be switched off while it is the last way back in.
This is the other half, and it is a different thing rather than a copy of the first (#149).

**The invariant that must not bend.** A person's own mail is never a route back into their
account. `accounts/recovery.py` reads *transports*; an outbox is a connected kind and not a
transport, so a person switching their own outbox off cannot lock themselves out. That is
true by construction rather than by anybody remembering it — the two halves cannot be
confused because they are not the same kind, and `policy.UNGOVERNED_KINDS` names only one of
them.

**Sending as somebody requires being them.** This is the half that *needs* per-person
settings: putting somebody else's address on a message leaving the instance's server is
spoofing, and SPF and DKIM will bounce it or bin it. So the message leaves over the person's
own server with the person's own address on it — their domain, their reputation, and their
bounces coming back to them, which is right.

**The sender is never rewritten.** A message whose ``From`` Postulo quietly replaced is a
message that looks forged, and looking forged is what gets a domain listed. If the outbox
and the message disagree about who is sending, that is an error rather than something to
paper over.
"""

from __future__ import annotations

import logging

from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

#: The setting that bounds how much mail one account may send. Per account, like every
#: other limit in `core/throttle.py`: this is the surface where a mistake is loudest,
#: because it reaches strangers rather than the person who made it.
RATE_SETTING = "POSTULO_OUTBOX_RATE"


class NoOutbox(Exception):
    """This person has no way to send as themselves, and the message says why."""


class WrongSender(Exception):
    """The message claims an address the outbox is not entitled to send from."""


def outbox_for(person):
    """The plugin that sends this person's mail, or ``None``.

    Governed like every other connected plugin: an administrator may switch it off, and
    then this returns nothing. What that means is *you cannot send from your own address
    here* — the instance still notifies, still recovers, still sends everything of its own.
    """
    from postulo.plugins.policy import plugins_for

    for plugin in plugins_for(person, "outbox"):
        return plugin
    return None


def can_send_as_themselves(person) -> bool:
    """Whether there is a connected, switched-on outbox for this person."""
    return connection_for(person) is not None


def connection_for(person):
    """The person's own outbox connection, if they have set one up and it is enabled."""
    from postulo.plugins.models import Connection

    plugin = outbox_for(person)
    if plugin is None:
        return None
    return (
        Connection.objects.for_user(person)
        .filter(kind="outbox", plugin=plugin.name, enabled=True)
        .first()
    )


def address_of(connection) -> str:
    """The address this connection is entitled to put on a message."""
    return (connection.config or {}).get("from_address", "")


def _is_djangos_fill_in(address: str) -> bool:
    from django.conf import settings

    return address == getattr(settings, "DEFAULT_FROM_EMAIL", "")


def send(person, message) -> int:
    """Send one message as ``person``. Raises rather than sending as somebody else.

    ``message`` is a Django ``EmailMessage``. Its ``from_email`` is either empty — in which
    case the outbox's own address is filled in — or it already says what the outbox is
    entitled to say. Anything else raises: quietly rewriting a sender is what makes mail
    look forged, and a mismatch here is a bug rather than a preference.
    """
    from postulo.core import throttle

    connection = connection_for(person)
    if connection is None:
        raise NoOutbox(
            str(
                _(
                    "You have no outbox set up, so there is nothing to send from your own "
                    "address with."
                )
            )
        )

    mine = address_of(connection)
    if not message.from_email or _is_djangos_fill_in(message.from_email):
        # Django puts DEFAULT_FROM_EMAIL on a message that was given no sender, so "no
        # sender" never actually arrives empty. A caller writing `EmailMessage(subject=…,
        # to=…)` meant *send this as me*, and treating the instance's own address as a
        # claim would refuse the ordinary call and, worse, teach callers to pass the
        # person's address by hand -- which is the sort of thing that gets got wrong once.
        message.from_email = mine
    elif message.from_email != mine:
        raise WrongSender(
            str(_("This outbox sends as %(address)s, not as %(other)s."))
            % {"address": mine, "other": message.from_email}
        )

    throttle.consume("outbox", person.pk, throttle.rate_for(RATE_SETTING))

    plugin = outbox_for(person)
    from postulo.plugins import consent

    if consent.wanted_by(plugin, connection.config) is not None:
        # Renewed here, where the connection is to hand, so the plugin is handed a token good
        # for the next minute rather than one that lapsed in the night. A grant the provider
        # will no longer renew goes up as `ConsentWithdrawn`, which says what to do about it
        # -- agree again -- where an SMTP refusal would say nothing useful (#151).
        consent.access_token(connection)
    sent = plugin.send(message, connection.full_config)
    # Never the recipients, never the body. What is useful in a log is that somebody sent
    # something and it went; who they wrote to is theirs.
    logger.info("Outbox %r sent %d message(s) for account %s", plugin.name, sent, person.pk)
    return sent
