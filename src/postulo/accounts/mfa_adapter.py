"""What a passkey is registered against, and what a person sees when they use one.

A passkey is bound to a **relying party**: a name and a domain, decided by the site at the
moment the key is made and checked by the browser every time afterwards. Two things follow,
and both are worth stating rather than discovering.

The **name** is what a password manager or a phone shows in the list of saved passkeys. Left
to allauth it is Django's site name, which on a fresh install is ``example.com``. An
operator who has named their instance should see that name instead, the way the
authenticator app already shows ``Postulo`` for a TOTP code.

The **domain** is the host the browser was on. A passkey made at ``postulo.example.org``
does not work at ``jobs.example.org``, and there is no migrating it: the key is held by the
device and the device will not offer it for a name it was not made for. That is a property
of the standard rather than of this code, and it is why the pages say so out loud.
"""

from __future__ import annotations

from allauth.mfa.adapter import DefaultMFAAdapter


class MFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self) -> dict[str, str]:
        """Name the instance, and stay on the host the browser is actually on.

        The id is left exactly as allauth computes it — the current host, minus the port.
        Pinning it to something configured would be a way to make every existing passkey
        stop working the day somebody changed a variable.
        """
        from postulo.core import site

        entity = super().get_public_key_credential_rp_entity()
        name = (site.instance_name() or "").strip()
        if name:
            entity["name"] = name
        return entity
