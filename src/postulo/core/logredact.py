"""Keeping a one-time secret out of the access log (#754).

A recovery link, an invitation, a password-reset link and an email-confirmation link carry
their secret in the path, because a link in a message has nowhere else to put it. The access
log is read by far more people than the message was meant for -- whoever collects logs, whoever
is shown a screenshot of them -- and a token written there is a token until it expires. So a
log line shows that the page was asked for and not which key opened it.

The same goes for the consent callback's query (a one-time code and the state that goes with
it) and for a ``Referer`` that carries any of these.
"""

from __future__ import annotations

import re

#: The paths whose next segment is a secret, then the segment: kept as the prefix and the rest
#: replaced, so the line still says which kind of link it was.
_SECRET_SEGMENT = re.compile(
    r"(?P<prefix>/accounts/(?:recover|invitation|confirm-email)/"
    r"|/accounts/password/reset/key/"
    r"|/accounts/login/code/confirm/)"
    r"(?P<secret>[^/?#\s\"]+)"
)
#: A callback's query is a one-time code and the state that goes with it, nothing to keep.
_CALLBACK_QUERY = re.compile(r"(?P<path>/settings/connections/consent/)\?[^\s\"]*")

REDACTED = "[redacted]"


def redact(text: str) -> str:
    """``text`` with the secrets in any address it holds replaced by ``[redacted]``."""
    if not text:
        return text
    text = _SECRET_SEGMENT.sub(lambda m: m.group("prefix") + REDACTED, text)
    return _CALLBACK_QUERY.sub(lambda m: m.group("path") + "?" + REDACTED, text)
