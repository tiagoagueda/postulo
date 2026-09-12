"""Several addresses on the web per holder, three kinds, and the one of each that counts.

Everything that reads a link goes through here rather than through the rows directly, for
the reason :mod:`postulo.core.phone_numbers` gives: the primary is the one to show, and the
kind's feature may be off for this person, in which case the primary is the only row that
exists as far as the interface, a CV and the API are concerned -- while every other row
stays exactly where it is, waiting to be offered again.

Three features over one table (#189). *Social profiles*, *repositories* and *websites* are
three separate decisions an administrator may take differently, so each kind has its own
plugin and its own block on the page, and this module is where a kind is matched to the
plugin that governs it. Nothing outside it needs to know which is which.
"""

from __future__ import annotations

from dataclasses import dataclass

from django import forms
from django.contrib.contenttypes import forms as generic_forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from postulo.plugins.repositories import REPOSITORIES
from postulo.plugins.social_profiles import SOCIAL_PROFILES
from postulo.plugins.websites import WEBSITES

from .models import WebLink

Kind = WebLink.Kind


@dataclass(frozen=True)
class Block:
    """One kind of link as the page presents it: a block of rows, or one box.

    The wording is whole sentences per kind rather than one sentence with the kind's name
    dropped into a slot, because a noun declined for a slot reads wrongly in most of the
    languages Postulo speaks (#168).
    """

    kind: str
    #: The feature plugin that governs whether several of this kind are offered.
    plugin: str
    #: The formset's prefix while several are offered, and so the POST keys.
    prefix: str
    #: The single box's field name while they are not.
    field: str
    legend: str
    intro: str
    #: The single box's label and help.
    label: str
    help_text: str
    #: What the page says about rows it is keeping and not showing, on the profile and on
    #: a contact.
    kept_back: str
    kept_back_contact: str


BLOCKS: tuple[Block, ...] = (
    Block(
        kind=Kind.SOCIAL,
        plugin=SOCIAL_PROFILES,
        prefix="social_profiles",
        field="social_profile",
        legend=_("Social profiles"),
        intro=_(
            "A LinkedIn, a Mastodon, a Bluesky — as many as are worth showing, with one "
            "of them marked as the one to show. Documents print that one."
        ),
        label=_("Social profile"),
        help_text=_("Your LinkedIn, or wherever a recruiter should look you up."),
        kept_back=_(
            "Some social profiles are kept and not shown, because <em>Several social "
            "profiles</em> is switched off. They come back untouched if it is switched on."
        ),
        kept_back_contact=_(
            "Some social profiles are kept for this contact and not shown, because "
            "<em>Several social profiles</em> is switched off."
        ),
    ),
    Block(
        kind=Kind.REPOSITORY,
        plugin=REPOSITORIES,
        prefix="repositories",
        field="repository",
        legend=_("Code repositories"),
        intro=_(
            "A profile on a forge, or one project worth showing — as many as are worth "
            "listing, with one of them marked as the one to show. Documents print that one."
        ),
        label=_("Code repository"),
        help_text=_("A profile on GitHub, Forgejo, GitLab or similar."),
        kept_back=_(
            "Some code repositories are kept and not shown, because <em>Several code "
            "repositories</em> is switched off. They come back untouched if it is switched on."
        ),
        kept_back_contact=_(
            "Some code repositories are kept for this contact and not shown, because "
            "<em>Several code repositories</em> is switched off."
        ),
    ),
    Block(
        kind=Kind.WEBSITE,
        plugin=WEBSITES,
        prefix="websites",
        field="website",
        legend=_("Websites"),
        intro=_(
            "A personal site, a blog, a portfolio — as many as are worth showing, with one "
            "of them marked as the one to show. Documents print that one."
        ),
        label=_("Website"),
        help_text=_("Your own site, if you have one."),
        kept_back=_(
            "Some websites are kept and not shown, because <em>Several websites</em> is "
            "switched off. They come back untouched if it is switched on."
        ),
        kept_back_contact=_(
            "Some websites are kept for this contact and not shown, because <em>Several "
            "websites</em> is switched off."
        ),
    ),
)

#: Every kind's value, in the order the page shows them.
KINDS: tuple[str, ...] = tuple(block.kind for block in BLOCKS)


def block_for(kind: str) -> Block:
    for block in BLOCKS:
        if block.kind == kind:
            return block
    raise KeyError(kind)


def several_allowed(person, kind: str) -> bool:
    """Whether this person may keep more than one link of this kind per holder."""
    from postulo.plugins.policy import decide

    return bool(decide(block_for(kind).plugin, person).on)


def offered_kinds(person) -> list[str]:
    """The kinds this person is offered several of, for a page deciding once."""
    return [kind for kind in KINDS if several_allowed(person, kind)]


def primary_for(holder, kind: str) -> WebLink | None:
    """The link to show for this holder and kind, or nothing when it has none."""
    return holder.web_links.filter(kind=kind, is_primary=True).first()


def primaries_for(holder) -> dict[str, WebLink | None]:
    """The link to show of each kind, keyed by kind, in one query."""
    found: dict[str, WebLink | None] = dict.fromkeys(KINDS)
    for row in holder.web_links.filter(is_primary=True):
        found[row.kind] = row
    return found


def links_for(holder, person, kind: str | None = None) -> list[WebLink]:
    """What to show for this holder: all of them, or the primary alone, per kind."""
    kinds = KINDS if kind is None else (kind,)
    shown: list[WebLink] = []
    for each in kinds:
        rows = holder.web_links.filter(kind=each)
        if not several_allowed(person, each):
            rows = rows.filter(is_primary=True)
        shown.extend(rows)
    return shown


def kept_back(holder, person) -> list[tuple[Block, int]]:
    """How many links of each kind are being kept and not shown, because its feature is off.

    Told to the person, so that "switched off" reads as *not offered* rather than as
    *gone*; only the kinds with something kept back are listed, so the page says nothing
    at all in the ordinary case.
    """
    found: list[tuple[Block, int]] = []
    for block in BLOCKS:
        if several_allowed(person, block.kind):
            continue
        count = holder.web_links.filter(kind=block.kind, is_primary=False).count()
        if count:
            found.append((block, count))
    return found


@transaction.atomic
def set_primary(link: WebLink) -> None:
    """Make this the holder's primary link of its kind, and the only one.

    Cleared first and set second, because the partial unique index means the two-primaries
    state cannot exist even for the length of a statement.
    """
    WebLink.objects.filter(
        content_type=link.content_type,
        object_id=link.object_id,
        kind=link.kind,
        is_primary=True,
    ).exclude(pk=link.pk).update(is_primary=False)
    if not link.is_primary:
        link.is_primary = True
        link.save(update_fields=["is_primary", "updated_at"])


@transaction.atomic
def save_only_link(holder, owner, kind: str, typed: str) -> WebLink | None:
    """Write the one address a person typed while the kind's feature is switched off.

    It edits the primary row of that kind and touches nothing else: the rows this person
    cannot currently see are not theirs to lose by saving a form that never showed them.

    Clearing the box removes the primary row, because that is an explicit act rather than
    a side effect of a switch. Nothing is promoted in its place -- a hidden link appearing
    as the visible one, because somebody emptied a field, would be the surprise this whole
    module exists to avoid.
    """
    typed = (typed or "").strip()
    primary = primary_for(holder, kind)
    if not typed:
        if primary is not None:
            primary.delete()
        return None
    if primary is None or primary.url != typed:
        # The same address may already be here as a row this person cannot see; then it
        # is that row that becomes the primary, rather than a refused duplicate.
        hidden = (
            holder.web_links.filter(kind=kind, url=typed)
            .exclude(pk=getattr(primary, "pk", None))
            .first()
        )
        if hidden is not None:
            set_primary(hidden)
            return hidden
    if primary is None:
        primary = WebLink(owner=owner, holder=holder, kind=kind, is_primary=True)
    primary.url = typed
    primary.save()
    return primary


# ------------------------------------------------------------- the one box on a form


def add_single_boxes(form: forms.Form, person, holder=None) -> None:
    """One URL box per kind whose feature is off, and none for a kind that is on.

    The rows of a kind that is on are a formset of their own, and two controls writing
    the same primary row would be two answers to one question.
    """
    for block in BLOCKS:
        if several_allowed(person, block.kind):
            continue
        box = forms.URLField(
            label=block.label,
            required=False,
            max_length=500,
            assume_scheme="https",
            help_text=block.help_text,
        )
        if holder is not None and holder.pk:
            primary = primary_for(holder, block.kind)
            box.initial = primary.url if primary else ""
        form.fields[block.field] = box


def single_boxes(form: forms.Form) -> list[forms.BoundField]:
    """The boxes `add_single_boxes` put on this form, for a template to lay out."""
    return [form[block.field] for block in BLOCKS if block.field in form.fields]


def save_single_boxes(form: forms.Form, holder, owner) -> None:
    for block in BLOCKS:
        if block.field in form.fields:
            save_only_link(holder, owner, block.kind, form.cleaned_data.get(block.field, ""))


# --------------------------------------------------------------- the rows on a page


class WebLinkForm(forms.ModelForm):
    """One row: the address, and what to call it.

    ``kind`` is not here because the formset is of one kind, and ``is_primary`` is not
    here for the reason the telephone rows give: the template renders one radio per row
    under a single name outside the formset's prefixes, so the browser enforces *exactly
    one* before anything is posted, and the formset reads the answer below.
    """

    class Meta:
        model = WebLink
        fields = ("label", "url")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["url"].assume_scheme = "https"


class BaseWebLinkFormSet(generic_forms.BaseGenericInlineFormSet):
    """The rows of one kind together: nothing listed twice for this holder."""

    #: Set by `formset_for`. Every row saved through this formset is of this kind.
    kind: str = ""

    def clean(self) -> None:
        super().clean()
        holder = self.instance
        already: set[str] = set()
        if holder is not None and holder.pk:
            already = set(holder.web_links.values_list("url", flat=True))
        seen: set[str] = set()
        for form in self.forms:
            if not form.is_valid() or form.cleaned_data.get("DELETE"):
                continue
            typed = (form.cleaned_data.get("url") or "").strip()
            if not typed:
                continue
            was = form.instance.url if form.instance.pk else ""
            if typed in seen or (typed in already and typed != was):
                form.add_error("url", _("This address is already listed."))
            seen.add(typed)

    def save_new(self, form, commit=True):
        form.instance.kind = self.kind
        return super().save_new(form, commit=commit)

    def save(self, commit: bool = True):
        """Save the rows, then settle which of them is the primary of this kind.

        The radio names a form prefix rather than a primary key, because a row being added
        for the first time has no key yet and somebody adding their first two links has to
        be able to say which is which.
        """
        saved = super().save(commit=commit)
        if not commit:
            return saved
        chosen = (self.data.get(f"{self.prefix}-primary") or "").strip()
        wanted = None
        for form in self.forms:
            if form.prefix == chosen and form.instance.pk and not form.cleaned_data.get("DELETE"):
                wanted = form.instance
                break
        if wanted is None:
            # Nobody said, so the holder keeps the primary it had; failing that, the first
            # row becomes it, because a holder with links and no primary shows none at
            # all once the feature is switched off again.
            mine = self.instance.web_links.filter(kind=self.kind)
            wanted = mine.filter(is_primary=True).first() or mine.first()
        if wanted is not None:
            set_primary(wanted)
        return saved


def formset_for(holder, kind: str, *, data=None, prefix: str | None = None):
    """The rows of one kind for one holder, ready to render or to save."""
    block = block_for(kind)
    factory = generic_forms.generic_inlineformset_factory(
        WebLink,
        form=WebLinkForm,
        formset=BaseWebLinkFormSet,
        extra=1,
        can_delete=True,
    )
    formset = factory(data=data, instance=holder, prefix=prefix or block.prefix)
    formset.kind = kind
    # Read by the template: the legend, the sentence under it, the kept-back note.
    formset.block = block
    # The holder's rows of every kind come back from the relation; only this kind's belong
    # in this block, and the rest are another block's or nobody's business.
    formset.queryset = formset.queryset.filter(kind=kind)
    return formset


def formsets_for(holder, person, *, data=None) -> list:
    """One formset per kind this person is offered several of, bound where posted.

    A form posted without a block -- an older client, a script -- means no change to that
    kind's rows, not an error about a missing management form.
    """
    found = []
    for block in BLOCKS:
        if not several_allowed(person, block.kind):
            continue
        posted = data is not None and f"{block.prefix}-TOTAL_FORMS" in data
        found.append(formset_for(holder, block.kind, data=data if posted else None))
    return found
