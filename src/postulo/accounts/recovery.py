"""A way back into an account that does not go through the post.

Postulo's only route back into an account has been an email, which makes mail the thing every
instance must keep working for ever — the interlock that refuses to switch the mail transport
off while it is the last way in is correct and, on an instance with no second route, permanent.

The smallest honest second route needs no third party at all: **an administrator issues a
single-use link and hands it over by whatever means they already trust** — in person, on the
telephone, through the chat the team already uses. No gateway, no vendor, no cost, and it is
the answer for the family or small-team instance where the administrator is in the same room.
It is the first of the three candidates in #103 and deliberately the least clever.

Four things it has to be, because a link like this is a whole account in a URL.

**Short-lived**, because a URL that works next week is a URL that has been in a chat log for
a week. **Single-use**, so a link forwarded to the wrong window cannot be replayed.
**Recorded**, because an administrator taking somebody's account back is exactly the act that
should leave a trace — the row below is that trace, and it survives the link being used.
**Shown once, to the administrator who asked for it**, and never mailed, logged or displayed
again: handing it over is the administrator's job and Postulo must not quietly do it over the
channel this exists to replace.

**It sets a password; it does not sign anybody in.** The smallest blast radius available. A
person who has one still has to sign in afterwards, still meets their second factor if they
have one, and a link that leaks is a password change on an account whose other factors are
untouched — not a session.
"""

from __future__ import annotations

import hashlib
import secrets

from django.utils.translation import gettext as _

#: 32 bytes, so guessing is not a strategy. Long enough that the URL is obviously a secret.
TOKEN_BYTES = 32


class Unusable(Exception):
    """This link cannot be used: spent, revoked, expired, or never existed.

    One exception for all four, deliberately. Telling somebody holding a bad link *which*
    kind of bad it is tells them whether it ever existed, and a link is a whole account.
    """


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def fingerprint(token: str) -> str:
    """What is stored. A link only ever needs checking, so nothing keeps the token itself.

    Unsalted SHA-256 rather than a password hash, and that is the right choice here: the
    input is 32 random bytes, so there is no dictionary to run and nothing for a salt to
    frustrate. A slow hash would only slow the person using their own link.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def issue(person, *, by):
    """Make a link for `person`, and return it with the one copy of its token.

    Any live link for the same person is revoked first. One at a time is simpler to reason
    about and simpler to explain: an administrator who issues a second link has decided the
    first one is not being used, and leaving both alive would mean a link somebody thought
    was superseded still opening the account.
    """
    from django.utils import timezone

    from .models import RecoveryLink

    token = new_token()
    RecoveryLink.objects.live().filter(person=person).update(revoked_at=timezone.now())
    link = RecoveryLink.objects.create(
        person=person,
        issued_by=by,
        token_fingerprint=fingerprint(token),
        expires_at=timezone.now() + RecoveryLink.LIFETIME,
    )
    return link, token


def find(token: str):
    """The live link this token opens, or raise. Never says which kind of no it is."""
    from .models import RecoveryLink

    if not token:
        raise Unusable(str(_("That link cannot be used.")))
    link = RecoveryLink.objects.live().filter(token_fingerprint=fingerprint(token)).first()
    if link is None:
        raise Unusable(str(_("That link cannot be used.")))
    return link


def spend(link, new_password: str) -> None:
    """Set the password and consume the link, in that order and only together.

    Consuming on the way *in* would let a link-preview bot in a chat application burn
    somebody's only way back into their account by fetching the URL, which is exactly the
    channel an administrator would hand it over on.
    """
    from django.db import transaction
    from django.utils import timezone

    with transaction.atomic():
        person = link.person
        person.set_password(new_password)
        person.save(update_fields=["password"])
        link.used_at = timezone.now()
        link.save(update_fields=["used_at"])


# ------------------------------------------------------- who this route reaches


def accounts_no_administrator_can_reach() -> int:
    """Active accounts an administrator could not issue a link for.

    An administrator can issue one for anybody but themselves: issuing needs signing in, and
    the person who has forgotten their password cannot. So:

    * no active administrator — nobody is reachable, and the count is every account;
    * two or more — everybody is, including each administrator, by another one;
    * exactly one — everybody except that administrator, who is reachable only by whatever
      else reaches them.

    Counted rather than assumed, the same way the passkey route is, because the answer
    changes the moment somebody is made an administrator and nothing should have to notice.
    """
    from allauth.mfa.models import Authenticator
    from django.contrib.auth import get_user_model

    people = get_user_model().objects.filter(is_active=True)
    administrators = list(people.filter(is_staff=True))
    if not administrators:
        return people.count()
    if len(administrators) > 1:
        return 0
    alone = administrators[0]
    has_a_passkey = Authenticator.objects.filter(
        user=alone, type=Authenticator.Type.WEBAUTHN
    ).exists()
    return 0 if has_a_passkey else 1
