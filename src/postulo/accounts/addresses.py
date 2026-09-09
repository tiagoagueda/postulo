"""Whether an account is offered the page for managing several email addresses.

Postulo's side of the `email-addresses` feature (#145). The plugin declares the capability;
this decides, for one person, whether the page is offered — which is the same division
`phone-numbers` uses, and the reason a plugin never asks whether it is itself switched on.

**Two floors sit below the policy, and both are about not stranding somebody.** The addresses
belong to allauth and switching a feature off deletes none of them, but hiding the page from
somebody who keeps a second address because the first is a work account they are about to
lose takes away their ability to promote it on the day they need to. #104 locks the last
transport for exactly this shape of mistake, one level up.
"""

from __future__ import annotations

from postulo.plugins.email_addresses import EMAIL_ADDRESSES


def is_offered(person) -> bool:
    """Whether this account may manage more than one address.

    * The policy says yes — the ordinary case.
    * Or the account **already has more than one**, and is therefore already relying on it.
    * Or its only address is **unverified**, and the page is what fixes that.

    Otherwise the page is not offered, and Postulo shows the primary address alone.
    """
    from postulo.plugins.policy import decide

    if decide(EMAIL_ADDRESSES, person).on:
        return True
    addresses = list(person.emailaddress_set.all())
    if len(addresses) > 1:
        return True
    return bool(addresses) and not addresses[0].verified
