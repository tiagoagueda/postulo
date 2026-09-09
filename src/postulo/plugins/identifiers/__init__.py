"""The registry of external identifiers, and which subjects each one identifies.

> another internal plugin : postulo-identifiers
> must include all identifiers from both companies as users with a ownership matrix so no
> valid company identifier is shown on the user data

**The separation asked for already existed, and was the least interesting part.** Two
registries kept two sets of choices, so a company scheme could not appear on a person — by
convention, because of which module a form imported from. A convention holds until somebody
wires a form up differently. It is a guarantee now: a scheme says which subjects it
identifies and the model refuses a row that disagrees, whatever route it arrived by (#109).

**What the separation cost is the interesting part.** The two sets are not disjoint. ISNI
identifies contributors *and* organisations by its own definition; Wikidata has items for
both; LinkedIn has profiles and company pages. Keeping them apart had quietly picked a side
for each, so a researcher could not record their Wikidata item and a university could not
record its ISNI — which is exactly the identifier an EU application form asks an institution
for. The matrix fixes that by construction rather than by remembering.

**Why this can be a plugin where contact details could not.** A scheme owns no rows.
`PersonIdentifier` and `CompanyIdentifier` stay in core, owned and migrated by core; what a
plugin contributes is a key, a label, a pattern, a checksum, a link, an example and the
subjects it applies to. #100 wanted a plugin to own a table, and plugins cannot.

**Not switchable off, and that is deliberate.** Every other plugin kind answers "is this on
for this person"; a registry answers "what does this key mean". Switching it off would leave
every stored identifier unreadable — no label, no link, no validation — which is not what
*off* means anywhere else in Postulo, so `identifier` is an ungoverned kind alongside
transports.

**Internal only, for now.** The kind advertises no entry-point group, so nothing outside
this process can register a scheme. That is not shyness about the idea — a plugin per
national company register is the obvious next thing, since ``register`` is one generic
scheme for SIRET, NIF, Companies House, KvK and Handelsregister alike — but a third-party
contract is a promise about breakage, and this one is not written yet.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from .schemes import SCHEMES

#: The identifier this plugin is known by. Not a policy key -- nothing decides about it.
IDENTIFIERS = "identifiers"


@declares(
    shipped(
        name=IDENTIFIERS,
        label=_("External identifiers"),
        kind="identifier",
        description=_(
            "Which external identifiers Postulo understands — ORCID, ISNI, Wikidata, a "
            "legal-entity identifier, a company register number — what each one should look "
            "like, where it links, and whether it identifies a person, an organisation or "
            "both. Nothing here is ever looked up online."
        ),
    )
)
class Identifiers:
    """A registry. Postulo asks it what a key means; it never asks anything of anyone."""

    #: Every scheme this plugin contributes. `core.identifiers.registry` merges these across
    #: whatever identifier plugins are installed, first registration keeping the key.
    schemes = tuple(SCHEMES.values())
