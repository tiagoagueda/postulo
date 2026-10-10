"""An address nobody has proved is a claim, and a claim does not last or block anybody (#891).

allauth keeps an address a member adds, unverified, on their own account until they remove
it, and a sign-up for that address by the person it belongs to is answered as if an account
already held it. Two rules put that right without taking anything from somebody who is
really confirming an address:

* **A claim expires.** Past the lifetime of the confirmation link mailed for it, an
  unverified address that is not the account's primary is removed. The link has stopped
  working by then, so the claim could no longer be proved by the way it was meant to be.
* **A sign-up displaces a claim.** Typing an address into the sign-up form removes the
  unverified, non-primary copies other accounts hold of it, so the form goes on to what it
  would have done had they never been made. Nothing is lost that was proved: only
  unverified copies go, and the claimant is told nothing about who tried.
"""

from __future__ import annotations

import datetime as dt

from allauth.account.models import EmailAddress
from django.conf import settings
from django.utils import timezone


def lifetime() -> dt.timedelta:
    """How long a claim lives: as long as the link mailed to confirm it (three days)."""
    return dt.timedelta(days=int(getattr(settings, "ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS", 3)))


def displace(email: str) -> int:
    """Remove every unverified, non-primary copy of ``email``; how many there were."""
    _total, by_model = EmailAddress.objects.filter(
        email__iexact=email, verified=False, primary=False
    ).delete()
    return by_model.get(EmailAddress._meta.label, 0)


def expire_unconfirmed(now: dt.datetime | None = None) -> int:
    """Remove the claims older than their lifetime; how many addresses went."""
    now = now or timezone.now()
    old = EmailAddress.objects.filter(
        claim__claimed_at__lte=now - lifetime(), verified=False, primary=False
    )
    _total, by_model = old.delete()
    return by_model.get(EmailAddress._meta.label, 0)
