"""When a second factor is asked for after somebody has already proved who they are.

allauth asks for one whenever the account has a TOTP app or a security key set up. Two
sign-ins do not need it, and for one of them allauth already knows.

**A passkey.** Signing in with one is two factors on its own — the device you have, released
by a fingerprint, a face or a PIN — so allauth skips the prompt after a passwordless
passkey login, and it is right to. There is nothing here for that: it is already the
behaviour, and a switch to *reinstate* the prompt would only put back the friction that
makes people turn two-factor authentication off altogether.

**Single sign-on.** The identity provider has just done the checking Postulo is about to
repeat, and on a company or university provider it very often did it with something
stronger than a six-digit code. But Postulo cannot know that: how the provider
authenticated somebody is not in what it sends back. So this is the operator's call, they
are the only one who knows what their provider enforces, and it is off until they make it.

What this never does: it does not remove anybody's TOTP, and it does not apply to a
password sign-in. Somebody who has a second factor and signs in with a password is asked
for it, always.
"""

from __future__ import annotations

from allauth.mfa.stages import AuthenticateStage
from django.http import HttpRequest


def arrived_through_the_provider(request: HttpRequest) -> bool:
    """Whether the most recent thing that authenticated this session was the provider.

    allauth writes a record per authentication method into the session as it happens, and
    the social flow writes its own before the stages run. The *last* record is the one that
    matters: a session that started with a password and later connected a provider was not
    opened by that provider.
    """
    from allauth.account.authentication import get_authentication_records

    records = get_authentication_records(request)
    if not records:
        return False
    return records[-1].get("method") == "socialaccount"


class SecondFactorStage(AuthenticateStage):
    """allauth's stage, with the operator's answer about their identity provider."""

    def _should_handle(self, request: HttpRequest) -> bool:
        if not super()._should_handle(request):
            return False

        from postulo.core import site

        if site.sso_is_second_factor() and arrived_through_the_provider(request):
            return False
        return True
