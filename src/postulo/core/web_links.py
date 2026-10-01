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

**A link is on a service** (#305): LinkedIn, a Mastodon server, somebody's Forgejo, or
*Other*. What a service is and which exist is `core.link_services`, a registry plugins
supply; what this module does with it is the row -- the choice, the name that is asked
for only under *Other*, and the check that an address filed under a service is one of that
service's addresses. The check is made where an address is taken in and never on a row
as it is stored, so nothing is refused in retrospect.
"""

from __future__ import annotations

from dataclasses import dataclass

from django import forms
from django.contrib.contenttypes import forms as generic_forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from postulo.plugins.repositories import REPOSITORIES
from postulo.plugins.social_profiles import SOCIAL_PROFILES
from postulo.plugins.websites import WEBSITES

from . import link_services
from .formsets import RowsAlreadyGone, leaving, owner_of, remove_first
from .models import WebLink

Kind = WebLink.Kind

#: What a row posts for *Other*, and what the stylesheet looks for to show the name box.
OTHER = link_services.OTHER


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
def ensure_one_primary(holder, kind: str) -> WebLink | None:
    """The holder keeps the primary of this kind it has; failing that, its first becomes it.

    What saving the rows does when nobody said which one is the primary, and what taking a
    row off *Your details* at once does afterwards (#303) -- one rule for both, so they
    cannot disagree about who inherits it. A holder with links and no primary shows none at
    all once the kind's feature is switched off again. The primary afterwards comes back,
    or nothing when no link of the kind is left.
    """
    mine = holder.web_links.filter(kind=kind)
    wanted = mine.filter(is_primary=True).first() or mine.first()
    if wanted is not None:
        set_primary(wanted)
    return wanted


@transaction.atomic
def save_only_link(holder, owner, kind: str, typed: str) -> WebLink | None:
    """Write the one address a person typed while the kind's feature is switched off.

    It edits the primary row of that kind and touches nothing else: the rows this person
    cannot currently see are not theirs to lose by saving a form that never showed them.

    Clearing the box removes the primary row, because that is an explicit act rather than
    a side effect of a switch. Nothing is promoted in its place -- a hidden link appearing
    as the visible one, because somebody emptied a field, would be the surprise this whole
    module exists to avoid.

    **One box has no choice of service**, so the address says it (#305): a new address is
    read as one pasted with nothing chosen, and takes the service whose host it is on, or
    *Other*. That can refuse nothing. An address left as it was keeps what it had.
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
    if primary.url != typed:
        primary.service, primary.label = link_services.settle(kind, "", typed, primary.label)
    primary.url = typed
    primary.save()
    return primary


# ------------------------------------------------------- links taken in without a page


def chosen_in_a_file(row, kind: str, url: str) -> str:
    """What a file's word for a link's service comes to, as the choice a row would post.

    A file is a claim: written by this instance last year, by another with plugins this
    one has not, or by hand (#305). So what it says is believed only as far as this
    instance can check it, and none of the three answers can refuse a link:

    - it says **nothing** -- a file from before links had services -- and the answer is
      nothing chosen, so the address says which service it is on;
    - it names **a service this kind offers here, and the address is one of its
      addresses**, and that is the service;
    - it says **anything else** -- blank, which is how *Other* is written; a key this
      instance does not know; a service the address is not on -- and the link is *Other*,
      which loses nothing but the word.
    """
    if not isinstance(row, dict) or "service" not in row:
        return ""
    said = row.get("service")
    named = link_services.find(said, kind) if isinstance(said, str) else None
    if named is not None and named.accepts((url or "").strip()):
        return named.key
    return OTHER


def checked_rows(rows) -> list[dict]:
    """Links handed over from outside a page -- the API -- each checked as a row is (#305).

    A row is a ``kind`` and a ``url``, and may say a ``service``, a ``label`` and whether
    it ``is_primary``. The service follows the rule the page does (`link_services.settle`):
    a named one has to be one the kind offers and the address one of its addresses,
    ``other`` takes any address, and none at all lets the address say. What comes back is
    what `add_links` writes; the first thing wrong is a `ValidationError` that names the
    address it is about, and nothing has been written.
    """
    found: list[dict] = []
    # Once under each kind: one address may be a repository and a website both (#457).
    seen: set[tuple[str, str]] = set()
    for row in rows:
        kind = str(row.get("kind") or "")
        url = str(row.get("url") or "").strip()
        if kind not in KINDS:
            raise ValidationError(
                _("“%(kind)s” is not one of the kinds of link.") % {"kind": kind[:40]},
                code="kind",
            )
        if not url:
            raise ValidationError(_("A link needs an address."), code="url")
        if (kind, url) in seen:
            listed = _("This address is already listed.")
            raise ValidationError(f"{url}: {listed}", code="duplicate")
        seen.add((kind, url))
        try:
            service, label = link_services.settle(
                kind, str(row.get("service") or ""), url, str(row.get("label") or "")
            )
        except ValidationError as error:
            raise ValidationError(f"{url}: {error.messages[0]}", code=error.code) from error
        found.append(
            {
                "kind": kind,
                "service": service,
                "label": label[:60],
                "url": url,
                "is_primary": bool(row.get("is_primary")),
            }
        )
    return found


@transaction.atomic
def add_links(holder, owner, rows: list[dict]) -> list[WebLink]:
    """Write the rows `checked_rows` passed, and settle the primary of each kind.

    The first row of a kind to ask for the primary gets it; where none asks, the holder
    keeps the primary it had, and failing that its first link of the kind becomes it --
    the rule the rows on the page follow.
    """
    made: list[WebLink] = []
    wanted: dict[str, WebLink] = {}
    for row in rows:
        link = WebLink.objects.create(
            owner=owner,
            holder=holder,
            kind=row["kind"],
            service=row["service"],
            label=row["label"],
            url=row["url"],
        )
        made.append(link)
        if row["is_primary"]:
            wanted.setdefault(link.kind, link)
    for kind in dict.fromkeys(link.kind for link in made):
        if kind in wanted:
            set_primary(wanted[kind])
        else:
            ensure_one_primary(holder, kind)
    return made


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


class ServiceSelect(forms.Select):
    """The choice of service, each option saying its icon and its hosts (#305).

    An ``<option>`` holds words and nothing else, so the chosen service's icon is drawn
    beside the closed select, by the server, and ``app.js`` keeps it in step from
    ``data-icon``. ``data-hosts`` is what lets the same script show a service the moment
    an address is pasted into a row where none was chosen. Shown, and not posted: the host
    is all the script reads, so the row goes with nothing chosen and saving decides, by
    the host and the shape, as it does with the script blocked.
    """

    def __init__(self, attrs=None, choices=()):
        super().__init__(attrs, choices)
        #: The attributes each option carries, by the option's value. The form fills it.
        self.facts: dict[str, dict[str, str]] = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        option["attrs"].update(self.facts.get(str(value), {}))
        return option


class WebLinkForm(forms.ModelForm):
    """One row: the service it is on, what to call it under *Other*, and the address.

    ``kind`` is not here because the formset is of one kind, and ``is_primary`` is not
    here for the reason the telephone rows give: the template renders one radio per row
    under a single name outside the formset's prefixes, so the browser enforces *exactly
    one* before anything is posted, and the formset reads the answer below.

    **The service is a choice of three sorts** (#305): one of the services the kind
    offers; *Other*, posted as ``other`` and stored as nothing; and, on a row being added,
    nothing chosen at all, which is where a new row starts. Saved like that, the address
    says which service it is (`link_services.settle`). A stored row has no such state:
    posted without a choice -- by a script, by a copy of the page from before there was
    one -- it keeps what it has.

    **A kind with no services has no choice to draw**, and the field is not on its form:
    a select holding *Other* alone would be a control that offers nothing. Websites are
    that kind as Postulo ships, and every row of theirs is *Other*.

    **Checked when it changes, and not otherwise.** A row saved again with its address
    and its service as they were is not checked against a pattern that may have changed
    since: nothing stored is refused in retrospect.
    """

    service = forms.ChoiceField(label=_("Service"), required=False, widget=ServiceSelect)

    class Meta:
        model = WebLink
        fields = ("service", "label", "url")

    def __init__(self, *args, kind: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        #: The kind of link the row is. The formset says; a row on its own carries it.
        self.link_kind = kind or self.instance.kind
        self.fields["url"].assume_scheme = "https"
        # Said once under the block rather than under every row, where it would stand
        # beneath a box that is only drawn for *Other*.
        self.fields["label"].help_text = ""
        offered = link_services.services_for(self.link_kind)
        if not offered:
            del self.fields["service"]
            return
        stored = bool(self.instance.pk)
        choices = [(key, service.label) for key, service in offered.items()]
        choices.append((OTHER, _("Other")))
        if not stored:
            choices.insert(0, ("", "—"))
        field = self.fields["service"]
        field.choices = choices
        field.widget.attrs["data-service-select"] = ""
        field.widget.facts = {
            "": {"data-icon": link_services.OTHER_ICON},
            OTHER: {"data-icon": link_services.OTHER_ICON},
            **{
                key: {"data-icon": service.icon_name, "data-hosts": " ".join(service.hosts)}
                for key, service in offered.items()
            },
        }
        if stored:
            # A row with no service is *Other*, and so is one whose service no installed
            # plugin knows any more; that one keeps its key unless somebody chooses.
            known = self.instance.service in offered
            self.initial["service"] = self.instance.service if known else OTHER

    @property
    def service_icons(self) -> list[dict]:
        """Every icon the row's choice can draw, and which of them is drawn now.

        All of them are on the page, hidden but for the chosen service's, because the
        script that follows the select has nowhere to fetch one from: it shows the one
        that is already there.
        """
        if "service" not in self.fields:
            return []
        facts = self.fields["service"].widget.facts
        chosen = facts.get(self["service"].value() or "", {}).get("data-icon")
        names = dict.fromkeys(fact["data-icon"] for fact in facts.values())
        return [{"name": name, "shown": name == chosen} for name in names]

    def clean(self):
        data = super().clean()
        url = data.get("url")
        if not url or self.has_error("service"):
            return data
        kind = self.link_kind
        stored = bool(self.instance.pk)
        kept = self.instance.service if stored else ""
        known = link_services.find(kept, kind) is not None
        # What a stored row is drawn with, and so what it posts back when nobody chooses.
        as_drawn = kept if known else OTHER
        chosen = data.get("service") or (as_drawn if stored else "")
        forgotten = bool(kept) and not known
        if stored and chosen == as_drawn and (forgotten or "url" not in self.changed_data):
            # As it was: an address and a service nobody touched are not checked again,
            # and a service nothing knows any more is kept for the day it comes back. A
            # name left on a row whose service says it already goes, as it does for a
            # telephone number or an identifier (#284).
            data["service"] = kept
            data["label"] = "" if known else data.get("label", "")
            return data
        try:
            data["service"], data["label"] = link_services.settle(
                kind, chosen, url, data.get("label", "")
            )
        except ValidationError as error:
            # Beside the address when the address is what is wrong with it.
            beside = "service" if error.code == "service" and "service" in self.fields else "url"
            self.add_error(beside, error)
        return data


class BaseWebLinkFormSet(RowsAlreadyGone, generic_forms.BaseGenericInlineFormSet):
    """The rows of one kind together: nothing listed twice in this block.

    Twice in *this* block, and not twice for the holder. The three blocks are three
    formsets, and none sees what another is adding in the same save, so a rule about the
    holder's every link could only be checked against the table -- where an address being
    added under two kinds at once is in neither yet. The table holds the rule this can
    keep: once under each kind (#457).

    A row the page still carries and the table no longer holds has already been removed
    (`RowsAlreadyGone`).
    """

    #: Set by `formset_for`. Every row saved through this formset is of this kind.
    kind: str = ""

    def clean(self) -> None:
        super().clean()
        holder = self.instance
        held: dict[int, str] = {}
        if holder is not None and holder.pk:
            # What will still be listed once this is saved, by the row that lists it: a row
            # being removed in the same save holds nothing, so its address is free for
            # another row to take (#461).
            staying = holder.web_links.filter(kind=self.kind).exclude(pk__in=leaving(self))
            held = dict(staying.values_list("pk", "url"))
        seen: set[str] = set()
        for form in self.forms:
            if not form.is_valid() or form.cleaned_data.get("DELETE"):
                continue
            typed = (form.cleaned_data.get("url") or "").strip()
            if not typed:
                continue
            # Compared with what the holder's *other* rows hold in the table, and never
            # with what this row's instance says it held: a ModelForm has written the
            # typed address onto the instance by now, so that was the row compared with
            # itself, and a stored row was refused nothing. Two rows exchanging their
            # addresses, three passing theirs round, and a copy of the page from before a
            # row was added each passed, and broke the table's rule on the way in.
            #
            # A row that stays holds its address until it is written, and the rows are
            # written in order, so an exchange cannot be made in one save and is refused
            # here; so is an address another row gives up only by changing.
            elsewhere = any(url == typed for key, url in held.items() if key != form.instance.pk)
            if typed in seen or elsewhere:
                form.add_error("url", _("This address is already listed."))
            seen.add(typed)

    def save_new(self, form, commit=True):
        # What the row is and whose it is, set where the row is made, so that no page
        # saving these rows has to remember to (#454).
        form.instance.kind = self.kind
        form.instance.owner = owner_of(self.instance)
        return super().save_new(form, commit=commit)

    def save(self, commit: bool = True):
        """Save the rows, then settle which of them is the primary of this kind.

        The radio names a form prefix rather than a primary key, because a row being added
        for the first time has no key yet and somebody adding their first two links has to
        be able to say which is which.

        The rows marked for removal go first, so the address one of them gives up can be
        taken by another row in the same save (#461).
        """
        if commit:
            remove_first(self)
        saved = super().save(commit=commit)
        if not commit:
            return saved
        chosen = (self.data.get(f"{self.prefix}-primary") or "").strip()
        wanted = None
        for form in self.forms:
            if form.prefix == chosen and form.instance.pk and not form.cleaned_data.get("DELETE"):
                wanted = form.instance
                break
        if wanted is not None:
            set_primary(wanted)
        else:
            # Nobody said, so the holder keeps the primary it had; failing that, the first
            # row becomes it.
            ensure_one_primary(self.instance, self.kind)
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
    formset = factory(
        data=data, instance=holder, prefix=prefix or block.prefix, form_kwargs={"kind": kind}
    )
    formset.kind = kind
    # Whether a row has a service to choose: the template draws the select only then.
    formset.has_services = bool(link_services.services_for(kind))
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
