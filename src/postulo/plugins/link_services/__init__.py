"""The services a web link can be on: the list Postulo ships (#305).

A social profile used to be an address and a free name. It is on a **service** now --
LinkedIn, a Mastodon server, GitHub, somebody's Forgejo -- chosen in the row, drawn with an
icon, and checked: an address filed under LinkedIn has to be a LinkedIn address.

**The list is a plugin and not a model's choices**, for the reason ``core.WebLink`` always
gave for having no list at all: brand names age. A registry can be added to without a
migration, by this package in a later release or by one installed beside it, and a row
whose service nothing knows any more reads as *Other* rather than as an error.

**There is always an Other**, which is not in this table: it is what a row with no service
is, with a free name and the web-address check alone.

**Only the table lives here.** The rows are ``core.WebLink``, the registry that merges this
table with anybody else's is ``core.link_services``, and this package imports the plugin
surface and nothing else -- it is written exactly as a package outside Postulo would write
its own.

**Not switchable off**, for the identifier registry's reason: a registry answers "what does
this key mean", and off would leave every stored service without a name or a check.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from .services import SERVICES

#: The identifier this plugin is known by. Not a policy key -- nothing decides about it.
LINK_SERVICES = "link-services"


@declares(
    shipped(
        name=LINK_SERVICES,
        label=_("Services for web links"),
        kind="link-service",
        description=_(
            "Which services a social profile or a code repository can be on — LinkedIn, "
            "Mastodon, Bluesky, GitHub, GitLab, Codeberg, a Forgejo instance — what an "
            "address on each one looks like, and the icon drawn beside it. An address on "
            "none of them is listed as “Other”. Nothing here is ever looked up online."
        ),
    )
)
class LinkServices:
    """A registry. Postulo asks it what a key means; it never asks anything of anyone."""

    #: Every service this plugin contributes. `core.link_services.registry` merges these
    #: with whatever other link-service plugins are installed.
    services = tuple(SERVICES.values())
