"""The external identifiers a company can carry — the registry, seen from the company's side.

A company in Postulo is a name in one person's account. An identifier ties that name to a
public record — a Wikidata item, a legal-entity identifier, a national register number, a
profile on a professional network — so two accounts, an importer or a plugin can say
"the same employer" without comparing spellings.

**The table used to live here, and a second copy of the machinery with it.** Both are gone:
there is one registry now, in `postulo.plugins.identifiers`, and each scheme says which
subjects it identifies. What is left is the company's view of it — and that is where the
guarantee lives, because every function below is scoped to `COMPANY` and cannot return a
person's scheme however it is called (#109).

An organisation can now record an **ISNI**, which most universities have and which is
exactly what an EU application form asks an institution for. It could not before, because
ISNI had been filed under people.

Nothing here touches the network; the person typing an identifier knows what they typed,
and looking it up is a deliberate action for later.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from postulo.core import identifiers as registry
from postulo.core.identifiers import COMPANY

#: The keys, re-exported so callers need not know which module the table lives in. They are
#: the same strings they always were; every stored row carries one.
WIKIDATA = "wikidata"
LEI = "lei"
REGISTER = "register"
LINKEDIN = "linkedin"
CRUNCHBASE = "crunchbase"
OPENCORPORATES = "opencorporates"
ISNI = "isni"
OTHER = "other"


def schemes() -> dict:
    """Every scheme that identifies an organisation."""
    return registry.schemes_for(COMPANY)


def choices() -> tuple[tuple[str, object], ...]:
    return tuple((key, scheme.label) for key, scheme in schemes().items())


def linked() -> tuple[str, ...]:
    """The keys whose values lead somewhere, for a table that offers to open them."""
    return tuple(key for key, scheme in schemes().items() if scheme.link)


def scheme_for(key: str):
    """One of them, or nothing — including when the key is a person's."""
    return registry.find(key, COMPANY)


def label_for(key: str) -> str:
    return registry.label_for(key, COMPANY)


def normalise(scheme_key: str, raw: str) -> str:
    """Tidy ``raw`` into the canonical spelling for its scheme."""
    scheme = scheme_for(scheme_key)
    return scheme.normalise(raw) if scheme else (raw or "").strip()


def validate(scheme_key: str, value: str) -> None:
    """Raise :class:`ValidationError` unless ``value`` is a well-formed identifier.

    A scheme that does not identify organisations is *unknown* here rather than merely
    wrong: an ORCID reaching this is an ORCID on a company, and there is no such scheme.
    """
    scheme = scheme_for(scheme_key)
    if scheme is None:
        raise ValidationError(_("Unknown identifier scheme."), code="scheme")
    if not scheme.pattern.match(value):
        raise ValidationError(
            _("That does not look like a %(scheme)s identifier (for example %(example)s)."),
            code="format",
            params={"scheme": scheme.label, "example": scheme.example},
        )
    if scheme.checksum is not None and not scheme.checksum(value):
        raise ValidationError(scheme.checksum_message, code="checksum")


def clean(scheme_key: str, raw: str) -> str:
    """Normalise then validate, returning the canonical value."""
    if scheme_for(scheme_key) is None:
        raise ValidationError(_("Unknown identifier scheme."), code="scheme")
    value = normalise(scheme_key, raw)
    validate(scheme_key, value)
    return value


def url_for(scheme_key: str, value: str) -> str:
    scheme = scheme_for(scheme_key)
    return scheme.url_for(value, COMPANY) if scheme else ""
