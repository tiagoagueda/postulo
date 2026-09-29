"""What every kind of external identifier knows about itself, including who it identifies.

A company has a Wikidata item or a legal-entity identifier; a person has an ORCID. The
machinery is the same for both: tidy what somebody pasted, say whether the result is
well-formed, and know where it links.

**The sentence this module used to open with was wrong in an interesting way.** It said the
things being identified have nothing in common, and kept the two registries in two files
that never had to agree. Three schemes identify people *and* organisations — ISNI says so
in its own definition, Wikidata has items for both, LinkedIn has profiles and company pages
— and the split had quietly picked a side for each. A researcher could not record their
Wikidata item; a university could not record its ISNI, which is the identifier an EU
application form asks an institution for (#109).

So a scheme now says **which subjects it identifies**, and three of them say both. That is
what makes "no company identifier appears on a person" a guarantee rather than a
convention: it used to rest on which module a form imported its choices from, and a
convention holds until somebody wires a form up differently. A subject that does not match
is refused at the model, by every route in.

Nothing here touches the network. The person typing an identifier knows what they typed,
and looking it up somewhere else is a deliberate act for another day. A plugin contributing
a scheme is not the loophole: a scheme says what a value should look like and where it
links, and that is the whole of what it may do.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

#: The two things an identifier can be *of*. Named rather than left as loose strings,
#: because every form, model and registry lookup keys on them.
PERSON = "person"
COMPANY = "company"
SUBJECTS = (PERSON, COMPANY)

#: The one kind a person or a company may hold more than once: a free slot with a name of
#: its own. The constraints on both identifier tables make the same exception.
OTHER = "other"


@dataclass(frozen=True)
class Scheme:
    key: str
    label: str
    #: What a well-formed value looks like, applied after normalisation.
    pattern: re.Pattern[str]
    #: Which of `SUBJECTS` this scheme can identify. The matrix, and the reason there is
    #: one: ISNI identifies contributors *and* organisations, and a registry that could not
    #: say so had to pick one and lose the other.
    subjects: frozenset[str] = frozenset(SUBJECTS)
    #: Where the value links, with ``{value}`` standing for it; empty when there is nowhere.
    #: This is the address for an organisation, and for every scheme whose one address
    #: serves both.
    link: str = ""
    #: Where it links for a *person*, where that is somewhere else. A LinkedIn company page
    #: and a personal profile are different addresses for the same scheme, which is exactly
    #: the sort of thing one registry has to be able to say and two could not (#109).
    person_link: str = ""
    #: Shown beside the input.
    example: str = ""
    #: Path prefixes that identify a pasted URL of this scheme, so the slug can be lifted.
    url_paths: tuple[str, ...] = ()
    #: The hosts those URLs live on; an address elsewhere is not an identifier of this kind.
    hosts: tuple[str, ...] = ()
    #: How many path segments make up the value. OpenCorporates keeps its jurisdiction and
    #: its number either side of a slash, and both are the identifier.
    segments: int = 1
    #: Whether letters are folded to upper case, or to lower.
    upper: bool = False
    lower: bool = False
    #: What this scheme does to a value beyond the folding above — regrouping an ORCID's
    #: digits, separating a register's country from its number. Runs after the URL is
    #: lifted and the case is folded, so it sees something close to canonical already.
    tidy: Callable[[str], str] | None = None
    #: Whether the value passes its own check digits, where it has some. A scheme with a
    #: checksum never needs a lookup to catch a typo, which is the entire reason it has one.
    checksum: Callable[[str], bool] | None = None
    #: What to say when the checksum fails, in this scheme's own terms.
    checksum_message: object = ""
    #: Who provides this scheme, for a page that lists them. Empty for Postulo's own.
    provider: str = field(default="")

    def identifies(self, subject: str) -> bool:
        return subject in self.subjects

    def url_for(self, value: str, subject: str = "") -> str:
        template = self.person_link if (subject == PERSON and self.person_link) else self.link
        return template.format(value=value) if template else ""

    def normalise(self, raw: str) -> str:
        """Tidy ``raw`` into this scheme's canonical spelling.

        Pasting is the common case, and for most of these it is the whole address — so the
        value is lifted out of a URL of this scheme's own host, folded, then handed to
        whatever the scheme itself does about spacing and grouping.
        """
        value = (raw or "").strip()
        lifted = value_from_url(value, self, segments=self.segments)
        if lifted is not None:
            value = lifted
        if self.lower:
            value = value.lower()
        if self.upper:
            value = value.upper()
        return self.tidy(value) if self.tidy else value


def hosted_by(url: str, scheme: Scheme) -> bool:
    """Whether ``url`` is on one of the scheme's own hosts.

    Checked before anything is lifted out of a pasted address, so a link on somebody
    else's site cannot be read as an identifier of this kind.
    """
    host = (urlsplit(url).hostname or "").lower()
    return any(host == known or host.endswith("." + known) for known in scheme.hosts)


def value_from_url(url: str, scheme: Scheme, *, segments: int = 1) -> str | None:
    """The identifier inside a pasted URL, or nothing if it is not one of the scheme's."""
    if "://" not in url or not scheme.url_paths or not hosted_by(url, scheme):
        return None
    parts = urlsplit(url)
    # GLEIF keeps the record in the fragment; everyone else in the path.
    haystack = parts.path + ("#" + parts.fragment if parts.fragment else "")
    for prefix in scheme.url_paths:
        if prefix in haystack:
            tail = haystack.split(prefix, 1)[1].strip("/")
            return "/".join(tail.split("/")[:segments])
    return None


# ------------------------------------------------------------------ the registry
#
# The schemes themselves live in a plugin. Postulo owns the *tables* -- `PersonIdentifier`
# and `CompanyIdentifier` are core models, migrated by core -- and a plugin contributes the
# registry: a key, a label, a pattern, a checksum, a link and the subjects it applies to. A
# scheme owns no rows, which is exactly why this one can be a plugin where contact details
# could not (#109).
#
# The itch behind it is `register`: one generic scheme for every national company register
# -- SIRET, NIF, Companies House, KvK, Handelsregister -- each with its own format and its
# own checksum, none of which Postulo validates. A plugin per country could.


def registry() -> dict[str, Scheme]:
    """Every scheme this instance knows, from every installed identifier plugin.

    Not cached here: `plugins.registry` already caches the plugin list, and a dictionary
    rebuilt from a handful of objects is cheaper than a cache somebody has to remember to
    clear when a plugin is switched on.
    """
    from postulo.plugins.registry import plugins

    found: dict[str, Scheme] = {}
    for plugin in plugins("identifier"):
        for scheme in getattr(plugin, "schemes", ()):
            found.setdefault(scheme.key, scheme)
    return found


def schemes_for(subject: str) -> dict[str, Scheme]:
    """The schemes that identify ``subject``, in registration order."""
    return {key: scheme for key, scheme in registry().items() if scheme.identifies(subject)}


def find(key: str, subject: str = "") -> Scheme | None:
    """One scheme by key, or nothing — and nothing if it does not identify ``subject``."""
    scheme = registry().get(key)
    if scheme is None or (subject and not scheme.identifies(subject)):
        return None
    return scheme


def label_for(key: str, subject: str = "") -> str:
    """A scheme's name in words, or its key where nothing recognises it.

    What ``get_scheme_display()`` used to do. It cannot any more, and that is the point: a
    label out of ``choices`` can only name a scheme compiled into the model, so a scheme
    from a plugin would have shown as a bare key everywhere it appeared.
    """
    scheme = find(key, subject)
    return str(scheme.label) if scheme is not None else key


#: How long a scheme key may be. The column has to hold it, and the keys are short words.
MAX_KEY_LENGTH = 20


@deconstructible
class Identifies:
    """Field validator: this scheme exists, and identifies what this row is about.

    The requested guarantee, made by construction. It used to rest on which module a form
    imported its choices from -- a convention, and a convention holds until somebody wires a
    form up differently. Here it travels with the column, so an LEI on a person is refused
    wherever it arrives from (#109).
    """

    def __init__(self, subject: str) -> None:
        self.subject = subject

    def __call__(self, value: str) -> None:
        scheme = registry().get(value)
        if scheme is None:
            raise ValidationError(_("Unknown identifier scheme."), code="scheme")
        if not scheme.identifies(self.subject):
            raise ValidationError(
                _("%(scheme)s does not identify this kind of thing."),
                code="subject",
                params={"scheme": scheme.label},
            )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Identifies) and other.subject == self.subject

    def __hash__(self) -> int:
        return hash((type(self), self.subject))


def scheme_field(subject: str) -> models.CharField:
    """The column an identifier's scheme lives in.

    Not ``choices``, which is what it was. Choices are frozen into every migration that
    touches the field, so a scheme contributed by a plugin could never be one -- and the
    keys are the same strings they always were, so every existing row keeps its value.
    """
    return models.CharField(
        _("scheme"), max_length=MAX_KEY_LENGTH, validators=[Identifies(subject)]
    )


def require(key: str, subject: str) -> None:
    """Refuse a scheme that does not identify ``subject``. Raises `ValidationError`.

    Called from `save()` on both identifier models, because *refused at the model* has to
    mean every route in and not only the two that run `full_clean`.
    """
    Identifies(subject)(key)


# ------------------------------------------------------------------ the rows on a form
#
# One identifier of each kind, Other excepted. Both tables have always held the rule, but the
# rows on *Your details* and on a company's form offered every kind on every row, and a
# second ORCID was found out only on saving -- on a company in words that did not say which
# kind, on a person in the constraint's own name. The rule is offered now as well as
# enforced, from here, for both forms (#307).


def _removing(row) -> bool:
    """Whether a row has *Remove* ticked, as posted or as drawn."""
    return "DELETE" in row.fields and bool(row["DELETE"].value())


def _set_to(row) -> str:
    """The kind a row is set to, as posted or as drawn; empty while none is chosen."""
    return row["scheme"].value() or ""


class SchemeSelect(forms.Select):
    """The choice of kind, with the kinds the other rows hold switched off (#307).

    ``<option disabled>`` needs no script, and a screen reader announces it as unavailable,
    so nothing more is said. The row's own kind is never switched off -- a row keeps what it
    has, even beside a duplicate about to be refused, or it could not be posted back -- and
    neither is Other, which may repeat.
    """

    def __init__(self, attrs=None, choices=()):
        super().__init__(attrs, choices)
        #: Asked when the select is drawn, for the kinds the other rows hold. The formset the
        #: row sits in answers; a row on its own has no neighbours.
        self.taken: Callable[[], Iterable[str]] = lambda: ()

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        off = set(self.taken()) - set(context["widget"]["value"]) - {OTHER}
        for _group, options, _index in context["widget"]["optgroups"]:
            for option in options:
                if option["value"] in off:
                    option["attrs"]["disabled"] = True
        return context


class OneOfEachKind:
    """Formset mixin for identifier rows: one of each kind, offered and enforced (#307).

    Worked out from the rows as they stand, posted or drawn from the table, because the
    formset is the only thing that sees all of them.

    - **Offered.** Each row's choice switches off the kinds the other rows hold. A row
      marked for removal holds nothing, so its kind is free for the others.
    - **Enforced.** A duplicate is still refused, because two new rows typed in one go can
      pick the same kind, and a page with its script is not the only way in. The refusal
      names the kind and sits on the row that lost it. A row keeping the kind it was saved
      with has first claim, and so does every saved row on the kind it was saved with while
      it is being changed, because the table holds that kind until the row is written;
      between rows taking a kind anew, the first keeps it.
    - **Written in an order that works.** Rows marked for removal go first, so the kind one
      of them gives up can be taken by another row in the same save.

    The rows' form carries `IdentifierRow`, which is where the refusal is asked for.
    """

    def add_fields(self, form, index):
        super().add_fields(form, index)
        form.fields["scheme"].widget.taken = lambda: self.held(besides=form)
        form.one_of_each_kind = self

    def held(self, besides=None) -> set[str]:
        """The kinds the rows hold, apart from ``besides``'s; never Other."""
        kinds = {_set_to(row) for row in self.forms if row is not besides and not _removing(row)}
        return kinds - {"", OTHER}

    def refuse_taken(self, row, key: str) -> None:
        """Raise `ValidationError` if another row has first claim on ``key``."""
        if key == OTHER or key == row.initial.get("scheme") or _removing(row):
            return
        ahead = True
        for other in self.forms:
            if other is row:
                ahead = False
            elif not _removing(other) and (
                other.initial.get("scheme") == key or (ahead and _set_to(other) == key)
            ):
                raise ValidationError(
                    _("%(scheme)s is already listed on another row."),
                    code="taken",
                    params={"scheme": label_for(key)},
                )

    def save(self, commit=True):
        # Django writes the saved rows in the order they are listed, removing or changing
        # each in turn, so a row taking the kind of one removed further down would reach
        # the table while that kind was still in it. Removed first, they are passed over
        # afterwards the way Django passes over any row already gone.
        if commit:
            for row in self.deleted_forms:
                if row.instance.pk is not None:
                    self.delete_existing(row.instance)
        return super().save(commit=commit)


class IdentifierRow:
    """Form mixin for one row of a `OneOfEachKind` formset (#307).

    The table's one-of-each-kind constraint, checked from one row, sees only the table,
    where a row about to be removed is still sitting, so it would refuse the kind that the
    removal frees -- and on a person it said so in the constraint's own name. The formset
    sees every row, so a row in one swaps that check for the formset's, which names the
    kind. Everything else the table promises is still checked here, and a row outside a
    formset is checked exactly as Django would check it.
    """

    def validate_constraints(self):
        rows = getattr(self, "one_of_each_kind", None)
        scheme = self.cleaned_data.get("scheme")
        if rows is None or not scheme or scheme == OTHER:
            return super().validate_constraints()
        exclude = self._get_validation_exclusions()
        try:
            self.instance.validate_constraints(exclude=exclude | {"scheme"})
        except ValidationError as error:
            self._update_errors(error)
        if "scheme" not in exclude:
            try:
                rows.refuse_taken(self, scheme)
            except ValidationError as error:
                self.add_error("scheme", error)
