"""The services a messaging handle can be on: the list Postulo ships (#682).

Somebody is reached on Signal, Matrix, Telegram or XMPP by a **handle** -- an identifier
the service gave them -- and a row holds one, on a **service** chosen in the row and
checked: a handle filed under Telegram has to be one Telegram allows.

**The list is a plugin and not a model's choices**, for the reason ``core.WebLink`` gave
for the same step (#305): networks age. A registry can be added to without a migration, by
this package in a later release or by one installed beside it -- an IRC network, a
company's own chat -- and a row whose service nothing knows any more reads as *Other*
rather than as an error.

**There is always an Other**, which is not in this table: a name of the person's own
choosing and any text for a handle.

**Only the table lives here.** The rows are ``core.MessagingHandle``, the registry that
merges this table with anybody else's is ``core.messaging_services``, and this package
imports the plugin surface and nothing else.

**Not switchable off**, for the registry reasons the identifier and link-service plugins
give: off would leave every stored service without a name or a check.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from .services import SERVICES

#: The identifier this plugin is known by. Not a policy key -- nothing decides about it.
MESSAGING_SERVICES = "messaging-services"


@declares(
    shipped(
        name=MESSAGING_SERVICES,
        label=_("Services for messaging handles"),
        kind="messaging-service",
        description=_(
            "Which messaging services a handle can be on — Matrix, XMPP, Signal, Telegram, "
            "Threema — and what a handle on each one looks like. A handle on none of them "
            "is listed as “Other”. Nothing here is ever looked up online."
        ),
    )
)
class MessagingServices:
    """A registry. Postulo asks it what a key means; it never asks anything of anyone."""

    #: Every service this plugin contributes. `core.messaging_services.registry` merges
    #: these with whatever other messaging-service plugins are installed.
    services = tuple(SERVICES.values())
