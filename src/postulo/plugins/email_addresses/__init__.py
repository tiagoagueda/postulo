"""Several email addresses per account: the feature, and the honest limit of what it owns.

> multiple e-mails shoud became an internal plugin as we did to the phone numbers and play
> the same role on the recovery of the user acount

Everything the request asks for already worked. `allauth.account.EmailAddress` gives an
account several addresses, exactly one `primary`, each `verified` independently and unique
across the instance — which is the shape #90 *copied* when telephone numbers were built. What
was missing was the plugin framing, and framing it turned out to raise a question worth
answering rather than a feature worth writing (#145).

**A feature here governs the page, not the data, and that is a real limit rather than a
shortcut.** The model belongs to a library: allauth's migrations are behind it and allauth's
own flows read it — verification, password reset, signing in by address, linking a social
account. A plugin that took it over would be a plugin owning somebody else's core model, and
a plugin that cannot verify an address cannot honestly own one. So *off* means Postulo offers
the primary address and stops offering the management page, and it deletes nothing because it
owns nothing.

**Is governing presentation a legitimate thing for a feature to do?** Yes, and the precedent
is the one the request names. `phone-numbers` off does not delete a number either: it shows
and uses the primary one and the rest stay recorded. A feature governs *what Postulo offers
and uses*, which is what both of these do. The difference is not the mechanism.

**The difference is what off costs, and here it could cost the account.** Somebody keeps a
second address because the first is a work account they are about to lose. Hiding the page
does not remove that address, but it removes their ability to promote it on the day they need
to — a lock-out arriving through a setting nobody thought was about recovery. #104 locks the
last transport for this exact shape of mistake, one level up. So there is a floor:
:func:`is_offered` never hides the page from an account that already has more than one
address, or from one whose only address is unverified. Somebody relying on the capability
keeps it, whatever the policy says.

**The visible change is a link disappearing**, and it is worth saying so plainly rather than
letting the plugin's presence imply more.

Only the declaration is here, as with `phone-numbers`: what Postulo *does* with the answer
lives in ``postulo.accounts.addresses``, because asking the policy is Postulo's job and a
plugin that asked whether it was on for somebody would be a plugin marking its own homework.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
EMAIL_ADDRESSES = "email-addresses"


@declares(
    shipped(
        name=EMAIL_ADDRESSES,
        label=_("Several email addresses"),
        kind="feature",
        description=_(
            "Keep more than one address on your account — a personal one, a work one — with "
            "any of them signing you in and one of them marked as the one Postulo writes to. "
            "Switched off, Postulo offers the primary address alone and stops offering the "
            "page for managing the rest. Nothing is deleted: the addresses stay, and an "
            "account that already has more than one keeps the page, because taking it away "
            "would take away the way back in on the day a work address stops working."
        ),
    )
)
class EmailAddressesFeature:
    """Several addresses per account, one of them primary.

    Off is what Postulo did before anybody thought about it: one address, the one on the
    account, which is what `user.email` has always been.
    """
