"""Every messaging service Postulo ships, and what a handle on each one looks like.

Only services whose identifier format is published are here (#682); everything else is
*Other*, which refuses nothing. **A pattern is matched whole against the handle once it is
normalised**, and is written to read a handle once: no repetition inside a repetition.

What was and was not read from the services' own pages is said beside each, so the next
person to touch one knows what to confirm before relying on it.

**Nothing here is looked up anywhere**, and no service has a mark: the icon is a generic
one out of the Lucide set (``TRADEMARKS.md``).
"""

from __future__ import annotations

import re

from postulo.plugins.api import MessagingService

MATRIX = "matrix"
XMPP = "xmpp"
SIGNAL = "signal"
TELEGRAM = "telegram"
THREEMA = "threema"


def _tidy(text: str) -> str:
    return " ".join((text or "").split())


def _matrix(text: str) -> str:
    """``@localpart:server``, lower-case: the current grammar's localpart is lower-case
    only, and a server name is a DNS name. The sigil is added where it was left off."""
    text = _tidy(text).lower()
    if text and not text.startswith("@") and ":" in text:
        text = "@" + text
    return text


def _xmpp(text: str) -> str:
    """A bare JID, lower-case (PRECIS folds a localpart's case; a domain is DNS). A typed
    ``xmpp:`` prefix and a ``/resource``, which names one device, are dropped."""
    text = _tidy(text)
    if text[:5].lower() == "xmpp:":
        text = text[5:].lstrip("/")
    return text.split("/", 1)[0].lower()


def _without_at(text: str) -> str:
    text = _tidy(text)
    return text[1:] if text.startswith("@") else text


def _threema(text: str) -> str:
    return _tidy(text).upper()


SERVICES: dict[str, MessagingService] = {
    service.key: service
    for service in (
        MessagingService(
            MATRIX,
            "Matrix",
            # @localpart:server -- a localpart of lower-case letters, digits and -.=_/+
            # (older IDs may hold anything but a colon: those are *Other*), and a server
            # that is a DNS name, an IPv4 address or a bracketed IPv6 one, with a port.
            re.compile(
                r"@[a-z0-9._=/+-]+:(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:.]+\])"
                r"(?::[0-9]{1,5})?"
            ),
            normaliser=_matrix,
            example="@name:example.org",
            icon="at-sign",
        ),
        MessagingService(
            XMPP,
            "XMPP",
            # A bare JID, local@domain (RFC 7622). What its PRECIS rules exclude from a
            # localpart is approximated by the characters the RFC lists as forbidden.
            re.compile(r"[^\s@/:<>\"'&]+@[^\s@/:<>\"'&]+"),
            normaliser=_xmpp,
            example="name@example.org",
            icon="at-sign",
        ),
        MessagingService(
            SIGNAL,
            "Signal",
            # A username: a nickname of 3 to 32 letters, digits and underscores that does
            # not start with a digit, a dot and a discriminator of at least two digits.
            # As relayed by secondary sources: confirm against Signal's support page.
            re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,31}\.[0-9]{2,12}"),
            normaliser=_without_at,
            example="name.42",
            icon="shield",
        ),
        MessagingService(
            TELEGRAM,
            "Telegram",
            # 5 to 32 letters, digits and underscores (Telegram's own page).
            re.compile(r"[A-Za-z0-9_]{5,32}"),
            normaliser=_without_at,
            example="@name_here",
            icon="send",
        ),
        MessagingService(
            THREEMA,
            "Threema",
            # An ID of 8 characters, upper-case letters and digits; a Gateway ID begins
            # with an asterisk. Threema's pages give the length and examples but do not
            # spell the character set out: confirm it.
            re.compile(r"[A-Z0-9]{8}|\*[A-Z0-9]{7}"),
            normaliser=_threema,
            example="ABCD1234",
            icon="smartphone",
        ),
    )
}
