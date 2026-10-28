"""Register lookup: asking a company register about a number, when a person presses for it.

The online half of #935, as option B there put it (#1021). The identifier module stays
offline and always on, as its contract says: it checks a register number's shape and check
digit and links to the register's page. This feature *reads* a company's register number or
LEI and offers *Look up* beside it, where an adapter answers for that country; the person sees
what the register said -- its name, its address, whether it is still trading, what it does --
and ticks what to keep. `postulo.jobs.register_lookup` is where the request is made and what
it is held to.

Two plugins in one package, because they are two kinds:

- **the feature**, ``register-lookup``, which decides whether anything is asked at all. It is
  **off until an administrator switches it on** (`plugins` migration 0012 writes the instance's
  row): a press tells a register, and so whoever runs it, which company somebody on this
  instance is looking at, and that is the operator's decision to make, not Postulo's;
- **the registry**, ``register-adapters``, of the registers Postulo ships an adapter for, which
  a package for another country adds to through ``postulo.register_adapters``. A registry is
  not switched on its own; the feature is the switch.

**Off is exactly what Postulo did before.** No button, no request; every name, address and
number already kept stays where it is.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from .adapters import ADAPTERS

#: The identifier the policy rows key on, fixed as a source's name is.
REGISTER_LOOKUP = "register-lookup"
#: The registry's. Not a policy key -- nothing decides about it.
REGISTER_ADAPTERS = "register-adapters"


@declares(
    shipped(
        name=REGISTER_LOOKUP,
        label=_("Register lookup"),
        kind="feature",
        icon="landmark",
        requires=("identifiers",),
        description=_(
            "Look a company up in its country's register when you press for it, from its "
            "register number (France, Norway, Finland, Czechia, Poland) or its LEI (GLEIF), "
            "and choose which of the official name, address, state and activity to keep. "
            "One request per press, carrying the number and nothing else, to the register's "
            "own service, which so learns which company somebody here is looking at. Off "
            "until an administrator switches it on; switched off, nothing is asked and "
            "nothing already kept is touched."
        ),
    )
)
class RegisterLookupFeature:
    """A company's register entry, read on a press and never otherwise."""


@declares(
    shipped(
        name=REGISTER_ADAPTERS,
        label=_("Company registers"),
        kind="register-adapter",
        description=_(
            "Which registers Register lookup can ask, one per country, and how each one's "
            "answer is read: the Annuaire des Entreprises (France), Enhetsregisteret (Norway), "
            "YTJ (Finland), ARES (Czechia), KRS (Poland) and GLEIF. Nothing here asks "
            "anything by itself."
        ),
    )
)
class RegisterAdapters:
    """A registry. Postulo asks it how a register is read; it never asks anything of anyone."""

    #: Every adapter this plugin contributes. `postulo.jobs.register_lookup.adapters` merges
    #: these with whatever other register-adapter plugins are installed.
    adapters = ADAPTERS
