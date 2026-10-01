"""Postal addresses: reading them off a holder, and keeping exactly one primary.

The same shape `core/phone_numbers.py` has, for the reason #92 gives — addresses were
specified as a companion to telephone numbers and the two are asked the same questions by
the same pages.

**What is different is uniqueness, and it is the whole reason this is a separate issue.** A
telephone number belongs to one person and is unique across the instance. An address does
not and is not: spouses share one, flatmates share one, an adult child at home shares one.
Unique per owner, so nobody lists the same address twice; freely shared between accounts,
because a household is not a mistake.

**What is different is also that nothing is verified.** Postulo is not going to post
anything, so it has no way to learn whether an address exists and no use for the answer.
Nothing here has a `verified_at`, and no address is ever a way back into an account.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import ClassVar

from django import forms
from django.contrib.contenttypes.forms import (
    BaseGenericInlineFormSet,
    generic_inlineformset_factory,
)
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .formsets import RowsAlreadyGone, owner_of
from .models import PostalAddress


def for_holder(holder):
    """Every address this person or contact holds, primary first."""
    if holder is None or not getattr(holder, "pk", None):
        return PostalAddress.objects.none()
    return PostalAddress.objects.filter(
        content_type=ContentType.objects.get_for_model(holder), object_id=holder.pk
    )


def primary_for(holder) -> PostalAddress | None:
    """The address to use for this holder, or nothing when it has none."""
    return for_holder(holder).filter(is_primary=True).first() or for_holder(holder).first()


@transaction.atomic
def make_primary(address: PostalAddress) -> None:
    """Move the primary flag to ``address``, leaving exactly one behind it.

    Two statements in one transaction rather than one save: the database constraint is what
    guarantees a single primary, and clearing the others first is how a change gets past it.
    Doing it in a form would leave every other writer -- the API, a command, a shell -- able
    to produce a state the constraint then refuses.
    """
    siblings = for_holder(address.holder).exclude(pk=address.pk)
    siblings.filter(is_primary=True).update(is_primary=False)
    if not address.is_primary:
        address.is_primary = True
        address.save(update_fields=["is_primary", "updated_at"])


@transaction.atomic
def ensure_one_primary(holder) -> PostalAddress | None:
    """After a removal, make sure something is primary if anything is left.

    A holder with addresses and no primary is a holder whose letter has no address on it,
    and that state is reachable by deleting the primary one. The oldest remaining takes it,
    which is the same rule the ordering already implies. The primary afterwards comes back,
    or nothing when no address is left: a row taken off *Your details* at once says which
    row the flag moved to (#303).
    """
    addresses = for_holder(holder)
    current = addresses.filter(is_primary=True).first()
    if current is not None:
        return current
    first = addresses.order_by("created_at", "pk").first()
    if first is None:
        return None
    first.is_primary = True
    first.save(update_fields=["is_primary", "updated_at"])
    return first


def location_line(holder) -> str:
    """A town and a country, from the primary address, for `Profile.location` to default to.

    **Never the street.** Guidance across most of Europe is that a CV carries a city and a
    country, partly because the street is irrelevant to the reader and partly because a
    precise address invites them to draw conclusions about somebody from where they live.
    `Profile.location` stays somebody's own overridable line; this is only what it starts
    from (#92), and `printed_location` below is where the two meet (#309).
    """
    from postulo.core import phones

    address = primary_for(holder)
    if address is None:
        return ""
    parts = [address.municipality.strip(), phones.country_name(address.country)]
    return ", ".join(part for part in parts if part)


def printed_location(profile) -> str:
    """Where somebody is, as a document prints it: what they typed, else `location_line`.

    The one rule every reader of `Profile.location` goes through (#309) -- the CV's header,
    the letter's sender block, their previews, the API's `printed_location` -- so that no
    two of them can disagree about a blank. **The blank is what is stored**: the line is
    worked out here each time, so a new primary address moves it without the profile being
    saved again, and a typed location always wins. Nothing at all when there is neither.
    """
    if profile is None:
        return ""
    typed = (getattr(profile, "location", "") or "").strip()
    return typed or location_line(profile)


# ------------------------------------------------------ what a country expects (#147)


def rules_apply(person) -> bool:
    """Whether the per-country rules act for this person.

    Postulo asks the policy; the plugin says what there is to ask about. Off means every
    address is free-form with neutral labels — which is the same code path a country with no
    row already takes, rather than a second one nobody has seen.
    """
    from postulo.plugins.policy import decide
    from postulo.plugins.postal_rules import POSTAL_RULES

    return bool(decide(POSTAL_RULES, person).on)


def label_for(part: str, country: str, *, person=None):
    """What to call one part of an address, in the reader's language."""
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        country = ""
    return postal_rules.label_for(part, country)


def warnings_for(address, *, person=None) -> list:
    """What looks unusual about an address for its country. Never enough to refuse a save."""
    return [sentence for _part, sentence in notes_for(address, person=person)]


def notes_for(address, *, person=None) -> list[tuple[str, object]]:
    """The same, each with the part it is about, so a page can leave out what it has
    already said about that part as an error."""
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        return []
    return postal_rules.notes_for(address)


def refusals_for(address, *, person=None) -> list[tuple[str, object]]:
    """What an address's own country rules out about it: the part, and what is expected.

    A postcode in a form the country never uses, and a part every address there carries
    left empty (#306). Nothing for a country the plugin has no rules for, and nothing for a
    person the rules are switched off for. ``address`` is anything with the five parts as
    attributes: a row as it is kept, or what was typed, before it is one.

    The empty part is asked of an address only. A town and a country with no street and no
    postcode are a place, which is what `location_line` prints on a CV, and nothing is
    required of one (`place_for`).
    """
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        return []
    return postal_rules.refusals_for(address)


def place_for(address, *, person=None) -> list[tuple[str, object]]:
    """What a whole address in that country also carries, where this one is a place: a town
    or a region with its country, and no street and no postcode.

    Notes, each with its part, and never a refusal: somebody who keeps no street in Postulo
    on purpose is not asked for one. Nothing for a whole address, for a country the plugin
    has no rules for, and while the rules are switched off.
    """
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        return []
    return postal_rules.place_for(address)


def canonical_postcode(postcode: str, country: str, *, person=None) -> str:
    """The postcode the way its country writes it, where it is plainly one of theirs.

    Case, spaces, hyphens and full stops: ``1000100`` is Portugal's ``1000-100``. As typed
    where it is not, where the country has no rules, and where the rules are off.
    """
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        return (postcode or "").strip()
    return postal_rules.canonical(postcode, country)


def lines_for(country: str, *, person=None) -> tuple[tuple[str, ...], ...]:
    """The parts of an address as the lines a form draws, in the country's own order."""
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        country = ""
    return postal_rules.lines_for(country)


def help_for(person=None):
    """What to say under the rows about what is refused, or nothing while the rules are off."""
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        return ""
    return postal_rules.HELP


def render(address, *, person=None) -> list[str]:
    """The address as lines, in the order its country writes them."""
    from postulo.plugins import postal_rules

    if person is not None and not rules_apply(person):
        return [part for part in address.one_line(chr(10)).split(chr(10)) if part]
    return postal_rules.render(address)


# --------------------------------------------------------------- the rows on a page


class CountrySelect(forms.Select):
    """A country dropdown whose options carry their flag for the script beside it.

    An `<option>` holds text and nothing else, so the flag cannot go in the list; it sits
    over the closed select and follows the choice (#88). Per option because static files
    are served under a content hash, so there is no pattern a script could build a URL
    from. The same answer the language menu gives since #208.
    """

    def create_option(self, name, value, *args, **kwargs):
        from postulo.core.flags import flag_url

        option = super().create_option(name, value, *args, **kwargs)
        code = str(value or "")
        if code:
            option["attrs"]["data-flag"] = flag_url(code)
        return option


@dataclass(frozen=True)
class Part:
    """One part of an address as a row draws it: its box, and what its country calls it."""

    name: str
    field: forms.BoundField
    label: object


@dataclass(frozen=True)
class Line:
    """The parts a country writes on one line, and which of them is the postcode."""

    parts: list[Part]
    #: The postcode's place on the line, counted from one, or nothing where it is not on it.
    narrow: int = 0


class PostalAddressForm(forms.ModelForm):
    """One row: what kind of address it is, and its parts.

    ``is_primary`` is deliberately not here, for the reason `phone_numbers.py` gives: a
    checkbox per row can be ticked twice, and the invariant would then be something the
    formset repairs after the fact. The template renders one radio per row under a single
    name outside the formset's prefixes, so the browser enforces *exactly one*.

    The labels here are the neutral ones. What a region is *called* depends on the address's
    country rather than on the reader's language — *State*, *Distrito*, *Prefecture* — and
    saying it properly is #147's job.

    ``person`` is whose page this is, and it is who the policy is asked about: with *Address
    rules by country* switched off for them, the row's parts keep their neutral names and
    nothing is said about its country. The switch used to be consulted by the helpers and
    by no page, because the form never said who was asking (#635). Nobody, where a form is
    built with no page behind it, and the rules then apply.

    **A row answers to its country before it is kept (#306).** What the country's own
    format rules out -- a postcode in a form it never uses, a part every address there
    carries left empty -- is an error beside that part, and a postcode that is plainly the
    country's own is written the country's way. Two things are left alone. A row already
    kept whose address has not changed is not asked again, so an address from before any of
    this, or from a file, never refuses a page it merely sits on; it is drawn marked
    (`kept_as_it_was`) and answers the next time it is changed. And ``refuses=False`` is a
    reader of files: the candidate file reads each address through this form and imports
    what it finds, marked, rather than refusing a file for it.

    **Two rows are not addresses, and neither is refused.** A town and a country with no
    street and no postcode are a place: kept, never marked, with a note under the row
    saying what a whole address there also carries (`a_place`). And a row being added with
    nothing typed in any of its boxes is an empty row whatever its menus show
    (`has_changed`): the country a new row starts on is the chooser's doing and not the
    person's, so such a row is not saved and nothing is said about it.
    """

    class Meta:
        model = PostalAddress
        fields = ("kind", "label", "street", "postcode", "municipality", "region", "country")

    #: The five parts that are the address. The kind and its name are about it, not of it.
    PARTS = ("street", "postcode", "municipality", "region", "country")
    #: The four of them somebody types. The country is chosen, and a new row starts on one.
    TYPED = ("street", "postcode", "municipality", "region")

    def __init__(
        self, *args, default_country: str = "", person=None, refuses: bool = True, **kwargs
    ):
        super().__init__(*args, **kwargs)
        from postulo.core import phones

        self.person = person
        self.refuses = refuses
        # The row as it is kept, read before anything posted is copied onto the instance:
        # what "changed" is measured against, and what the mark is about.
        self._kept = (
            SimpleNamespace(**{part: getattr(self.instance, part) for part in self.PARTS})
            if self.instance.pk
            else None
        )
        self._kept_comparable = self.instance.comparable_form() if self.instance.pk else ""
        self._asked = False
        # Two rows, because a street address is two lines in plenty of places and one box
        # of one line quietly asks somebody to leave the second out. `autocomplete` names
        # each part's purpose (SC 1.3.5, #276): these are the person's own addresses, and a
        # browser that knows them can fill them.
        self.fields["street"].widget = forms.Textarea(
            attrs={"rows": 2, "autocomplete": "street-address"}
        )
        for name, token in (
            ("postcode", "postal-code"),
            ("municipality", "address-level2"),
            ("region", "address-level1"),
        ):
            self.fields[name].widget.attrs["autocomplete"] = token

        self.fields["country"] = forms.ChoiceField(
            label=_("country"),
            required=False,
            choices=[("", "—"), *phones.country_choices()],
            # Each option carries its flag's URL and the select is marked for the script
            # that draws the chosen flag over it, as the telephone field's chooser is
            # (#88, #214). The server draws the flag the row loaded with, so the field is
            # right with scripts off.
            widget=CountrySelect(attrs={"autocomplete": "country-name", "data-flag-select": ""}),
        )
        if not self.instance.pk and default_country:
            self.fields["country"].initial = default_country
        for name in self.TYPED:
            self.fields[name].required = False

    def has_changed(self) -> bool:
        """Whether there is a row here to keep, for a row being added.

        A row with nothing typed in any of its four boxes is an empty row, in every
        country. Django asks whether anything differs from what the row was drawn with, and
        a country or a kind chosen on a row nobody wrote in does: that row was then checked
        as an address -- four errors under a row holding only *United States* -- or, for a
        country with no rules, saved holding only its country. A row already kept is
        Django's to answer for: one that holds only a country is left alone.
        """
        if self.is_bound and self._kept is None:
            typed = (self.data.get(self.add_prefix(name)) or "" for name in self.TYPED)
            if not any(value.strip() for value in typed):
                return False
        return super().has_changed()

    def clean(self):
        data = super().clean()
        if data.get("kind") == PostalAddress.Kind.OTHER:
            if not (data.get("label") or "").strip():
                self.add_error("label", _("Say what this address is."))
        else:
            # Blanked with the kind that made it meaningless (#284).
            data["label"] = ""
        if self.refuses and self._address_changed(data):
            self._answer_to_its_country(data)
        return data

    def _address_changed(self, data: dict) -> bool:
        """Whether what was posted is a different address from the one the row keeps.

        Always, for a row being added. For a kept one the question is the model's own, its
        comparable form, and not Django's `changed_data`: a browser posts a two-line street
        with its own line endings, which `changed_data` calls a change in a row nobody
        touched, and a kind or a name is no part of the address at all.
        """
        if self._kept is None:
            return True
        typed = PostalAddress(**{part: data.get(part) or "" for part in self.PARTS})
        return typed.comparable_form() != self._kept_comparable

    def _answer_to_its_country(self, data: dict) -> None:
        """Write the postcode the country's way, and refuse what its format rules out.

        Here rather than in `_post_clean`, where the notes are, because the postcode that
        is kept has to be the one written the country's way, and it is the cleaned data that
        is copied onto the instance.

        A part its own field has already refused -- too long, or holding a character no
        text may -- is not in the cleaned data, and is not empty either: it is said once,
        by the field, and not a second time as a part left out.
        """
        self._asked = True
        country = (data.get("country") or "").strip().upper()
        if "postcode" in data:
            data["postcode"] = canonical_postcode(
                data.get("postcode") or "", country, person=self.person
            )
        typed = SimpleNamespace(**{part: data.get(part) or "" for part in self.PARTS})
        for part, sentence in refusals_for(typed, person=self.person):
            if part not in self.errors:
                self.add_error(part, sentence)

    #: What this row's own country would usually expect. Filled by `_post_clean`.
    country_notes: ClassVar[list] = []

    def _post_clean(self):
        """Notes rather than errors, and after the instance carries what was typed.

        What is only *usually* so is never `add_error`: an address that fits no rule is
        still where somebody lives. `clean()` is too early — a ModelForm copies the cleaned
        data onto the instance here, so asking before this point asks about the row as it
        was loaded (#147). A part is not remarked on where it has already been spoken of:
        by its error, by the mark on a row that was left alone, or by what is said under a
        row that is a place.
        """
        super()._post_clean()
        said = set(self.errors) | {part for part, _sentence in self._left_alone()}
        said |= {part for part, _sentence in place_for(self._shown(), person=self.person)}
        self.country_notes = [
            sentence
            for part, sentence in notes_for(self.instance, person=self.person)
            if part not in said
        ]

    def _left_alone(self) -> list[tuple[str, object]]:
        """What the row's country would refuse about the address as it is kept, where the
        row has not been asked: each part, and the sentence."""
        if self._kept is None or self._asked or not self.refuses:
            return []
        return refusals_for(self._kept, person=self.person)

    @property
    def kept_as_it_was(self) -> list:
        """What the row's country would refuse about the address **as it is kept**.

        The mark a template draws on a row that is left alone: an address kept before
        addresses answered to their country, or brought by an archive, a candidate file or
        a Europass file, none of which refuses a file for an address. Empty for a row being
        added, for one whose address was changed in this submission -- that one has
        answered, with its errors beside its parts -- and while the rules are off.
        """
        return [sentence for _part, sentence in self._left_alone()]

    def _shown(self) -> SimpleNamespace:
        """The five parts as the row's boxes show them: what was posted, or what is kept."""
        return SimpleNamespace(**{part: str(self[part].value() or "") for part in self.PARTS})

    @property
    def a_place(self) -> list:
        """What a whole address in the row's country also carries, where the row is a place.

        A town or a region with its country, and no street and no postcode: *Lisboa,
        Portugal*, which is what a CV shows and all that somebody who keeps no street here
        on purpose types. The row is kept as it is and is never marked -- it is not an
        address that fails, it is a shorter thing, allowed -- and these are the sentences a
        template draws under it, so that somebody who did mean a whole address knows what
        one has in that country. Empty for a whole address, for the empty row a page ends
        with, for a country with no rules, and while the rules are off.
        """
        return [sentence for _part, sentence in place_for(self._shown(), person=self.person)]

    def _country_shown(self) -> str:
        """The country the row's select shows: what was posted, what is kept, or the one a
        new row starts on."""
        return str(self["country"].value() or "").strip().upper()

    def labels_for_country(self) -> dict:
        """What to call each part, for the country currently chosen on this row."""
        country = self._country_shown()
        return {
            part: label_for(part, country, person=self.person)
            for part in ("postcode", "municipality", "region")
        }

    def lines(self) -> list[Line]:
        """The parts as the lines the row draws, in the order its country writes them.

        Each part with its box and with what its country calls it. The postcode is the one
        short part, and a line says where it sits so the stylesheet can keep its column
        narrow whichever end of the line a country writes it at.
        """
        country = self._country_shown()
        found = []
        for names in lines_for(country, person=self.person):
            parts = [
                Part(name, self[name], label_for(name, country, person=self.person))
                for name in names
            ]
            narrow = names.index("postcode") + 1 if "postcode" in names else 0
            found.append(Line(parts, narrow))
        return found


class BasePostalAddressFormSet(RowsAlreadyGone, BaseGenericInlineFormSet):
    """The rows together: nothing listed twice **by this person**.

    Only by this person. Addresses are not unique across the instance and must not be: two
    people at one address is a household, and refusing the second would also disclose that
    somebody else on this server lives there (#92).

    A row the page still carries and the table no longer holds has already been removed
    (`RowsAlreadyGone`).

    **The constraint is per owner and the page is per holder**, and the rows used to be
    compared only with one another, so two ordinary edits got past the form and were
    answered by the database with a 500 (#458). Somebody moving house changed their first
    address into their second and removed the second: the first row's UPDATE ran while the
    second still existed. And an address typed on *Your details* that a contact of theirs
    already held met nothing in the form to say so. So the form now answers for everything
    the constraint will ask -- what the account lists anywhere outside these rows -- and
    the rows are written in an order in which none is in another's way.
    """

    #: Whose rows these are while there is no holder to ask. Set by `formset_for`.
    person = None

    def _listed_elsewhere(self) -> set[str]:
        """What this account already lists that is not one of the rows on this page.

        Another holder's -- a contact's, which an archive brings -- and this holder's own
        where the page does not carry it: a row added from a second tab since this copy
        was drawn. The rows the page does carry are left out whole, kept and removed
        alike, because what they will hold after the save is what was posted, and that is
        compared below.
        """
        holder = self.instance
        owner = owner_of(holder) if holder is not None and holder.pk else self.person
        if owner is None:
            return set()
        here = [form.instance.pk for form in self.initial_forms if form.instance.pk]
        return set(
            PostalAddress.objects.filter(owner=owner)
            .exclude(comparable="")
            .exclude(pk__in=here)
            .values_list("comparable", flat=True)
        )

    def clean(self) -> None:
        super().clean()
        elsewhere = self._listed_elsewhere()
        seen: set[str] = set()
        for form in self.forms:
            if not form.is_valid() or form.cleaned_data.get("DELETE"):
                continue
            comparable = form.instance.comparable_form()
            if not comparable:
                continue
            if comparable in seen or comparable in elsewhere:
                form.add_error(None, _("This address is already listed."))
            seen.add(comparable)

    def save_new(self, form, commit=True):
        # Whose the row is, set where the row is made, so that no page saving these rows
        # has to remember to (#454).
        form.instance.owner = owner_of(self.instance)
        return super().save_new(form, commit=commit)

    def save_existing_objects(self, commit=True):
        """The rows already kept: the removals first, then the changes.

        Django writes them in the order of the page, and the address somebody changes a
        row *into* may be one a later row still holds: the row being removed in the same
        save, or a row being changed into something else. `clean()` has already agreed
        that no two rows end up alike, so the collision is only ever with a value that is
        on its way out, and it is taken out of the way first: the removed rows are
        deleted, and every row about to change gives up its comparable form, which an
        empty one never collides on. Each then takes its new one as it is saved (#458).
        """
        if not commit:
            return super().save_existing_objects(commit=commit)
        removing = self.deleted_forms
        gone = []
        for form in self.initial_forms:
            row = form.instance
            if row.pk is not None and form in removing:
                gone.append(row)
                self.delete_existing(row, commit=True)
        changing = [
            form.instance.pk
            for form in self.initial_forms
            if form.instance.pk is not None and form not in removing and form.has_changed()
        ]
        if changing:
            PostalAddress.objects.filter(pk__in=changing).update(comparable="")
        saved = super().save_existing_objects(commit=True)
        # Django passes over a row with no key, which is what a deleted one is by now.
        self.deleted_objects = gone + self.deleted_objects
        return saved

    @transaction.atomic
    def save(self, commit: bool = True):
        """Save the rows, then settle which of them is the primary.

        The radio names a form prefix rather than a primary key, because a row being added
        for the first time has no key yet. In one transaction, so that a row which gave up
        its comparable form to let another past never keeps the empty one.
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
        if wanted is not None:
            make_primary(wanted)
        else:
            ensure_one_primary(self.instance)
        return saved


def formset_for(
    holder, *, default_country: str = "", data=None, prefix: str = "addresses", person=None
):
    """The rows for one holder, ready to render or to save.

    ``person`` is who the policy is asked about, for *Address rules by country* (#635).
    Left out, it is the account behind the holder -- a profile's user, a contact's owner --
    which is the only account that ever sees these rows, so a page that forgets to say who
    is asking still honours the switch.
    """
    if person is None and holder is not None and holder.pk:
        person = owner_of(holder)
    factory = generic_inlineformset_factory(
        PostalAddress,
        form=PostalAddressForm,
        formset=BasePostalAddressFormSet,
        extra=1,
        can_delete=True,
    )
    formset = factory(
        data=data,
        instance=holder,
        prefix=prefix,
        form_kwargs={"default_country": default_country, "person": person},
    )
    formset.person = person
    # Read by the template: what is refused and how forgiving a postcode is, said once under
    # the rows, and not at all while the rules are off for this person (#306).
    formset.rules_help = help_for(person)
    return formset
