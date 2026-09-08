"""Several telephone numbers per holder, and the one that counts.

Everything that reads a number goes through here rather than through the rows directly,
because two things have to be true at every such point and neither is visible in the
table. The primary is the number to use. And the feature may be switched off for this
person, in which case the primary is the only number that exists as far as the interface,
a CV, a letter and the API are concerned — while every other row stays exactly where it
is, waiting to be offered again.

That second part is the whole reason this module exists instead of a few queryset calls
scattered about. "Off shows the primary" is one sentence, and it has to be one sentence in
the code too, or it becomes a rule that six call sites remember and a seventh does not.
"""

from __future__ import annotations

from django import forms
from django.contrib.contenttypes import forms as generic_forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from . import phone_field, phones
from .features import PHONE_NUMBERS
from .models import PhoneNumber

#: What somebody is told when the number they typed is already recorded here.
#:
#: It names the disclosure rather than hiding it. Instance-wide uniqueness cannot be
#: enforced without telling whoever typed a number that somebody else may hold it, and a
#: vaguer message would not remove that — it would only leave the person guessing at what
#: had happened while disclosing exactly as much.
ALREADY_IN_USE = _(
    "That number is already recorded on this instance. Telephone numbers are kept unique "
    "across every account here, so it may belong to somebody else's records."
)


def several_allowed(person) -> bool:
    """Whether this person may keep more than one number per holder."""
    from postulo.plugins.policy import decide

    return bool(decide(PHONE_NUMBERS, person).on)


def primary_for(holder) -> PhoneNumber | None:
    """The number to use for this holder, or nothing when it has none."""
    return holder.phone_numbers.filter(is_primary=True).first()


def numbers_for(holder, person) -> list[PhoneNumber]:
    """What to show for this holder: all of them, or the primary alone."""
    if several_allowed(person):
        return list(holder.phone_numbers.all())
    primary = primary_for(holder)
    return [primary] if primary else []


def kept_back(holder, person) -> int:
    """How many numbers are being kept and not shown, because the feature is off.

    Shown to the person, so that "switched off" reads as *not offered* rather than as
    *gone*. Somebody who cannot see their four numbers and is not told they still exist
    has every reason to assume switching a plugin off deleted them.
    """
    if several_allowed(person):
        return 0
    return holder.phone_numbers.exclude(is_primary=True).count()


def taken_elsewhere(number: str, *, exclude_pk: int | None = None) -> bool:
    """Whether some other row on this instance already holds this number.

    Only a number that reached international form can be compared at all; one that did not
    is kept as typed and takes part in nothing, which is the rule ``phones.py`` sets and
    this follows rather than reinvents.
    """
    normalised = phones.normalise(number)
    if not normalised:
        return False
    rows = PhoneNumber.objects.filter(normalised=normalised)
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return rows.exists()


@transaction.atomic
def set_primary(number: PhoneNumber) -> None:
    """Make this the holder's primary number, and the only one.

    Cleared first and set second, because the partial unique index means the two-primaries
    state cannot exist even for the length of a statement.
    """
    PhoneNumber.objects.filter(
        content_type=number.content_type, object_id=number.object_id, is_primary=True
    ).exclude(pk=number.pk).update(is_primary=False)
    if not number.is_primary:
        number.is_primary = True
        number.save(update_fields=["is_primary", "updated_at"])


@transaction.atomic
def save_only_number(holder, owner, typed: str) -> PhoneNumber | None:
    """Write the one number a person typed while the feature is switched off.

    It edits the primary row and touches nothing else: the numbers this person cannot
    currently see are not theirs to lose by saving a form that never showed them.

    Clearing the box removes the primary row, because that is an explicit act rather than
    a side effect of a switch. Nothing is promoted in its place — a hidden number
    appearing as the visible one, because somebody emptied a field, would be the surprise
    this whole module exists to avoid.
    """
    typed = (typed or "").strip()
    primary = primary_for(holder)
    if not typed:
        if primary is not None:
            primary.delete()
        return None
    if primary is None:
        primary = PhoneNumber(owner=owner, holder=holder, is_primary=True)
    primary.number = typed
    primary.save()
    return primary


# --------------------------------------------------------------- the rows on a page


class PhoneNumberForm(forms.ModelForm):
    """One row: what kind of number it is, and the number.

    ``is_primary`` is deliberately not here. A checkbox per row can be ticked twice, and
    the invariant would then be something the formset repairs after the fact. The template
    renders one radio per row under a single name outside the formset's prefixes, so the
    browser enforces *exactly one* before anything is posted, and the formset reads the
    answer below.
    """

    class Meta:
        model = PhoneNumber
        fields = ("kind", "label", "number")

    def __init__(self, *args, default_country: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["number"] = phone_field.PhoneField(
            label=_("Phone"),
            required=False,
            default_country=default_country,
            help_text=_(
                "Kept in the international form, so it can be dialled from anywhere. A "
                "number that already starts with + is taken as it is."
            ),
        )
        if self.instance and self.instance.pk:
            self.fields["number"].initial = self.instance.number

    def clean(self):
        data = super().clean()
        if data.get("kind") == PhoneNumber.Kind.OTHER and not (data.get("label") or "").strip():
            self.add_error("label", _("Say what this number is."))
        return data


class BasePhoneNumberFormSet(generic_forms.BaseGenericInlineFormSet):
    """The rows together: nothing listed twice, here or anywhere else on the instance."""

    def clean(self) -> None:
        super().clean()
        seen: set[str] = set()
        for form in self.forms:
            if not form.is_valid() or form.cleaned_data.get("DELETE"):
                continue
            typed = (form.cleaned_data.get("number") or "").strip()
            if not typed:
                continue
            normalised = phones.normalise(typed)
            if not normalised:
                # Unparseable, so incomparable, so it collides with nothing. Kept as typed.
                continue
            if normalised in seen:
                form.add_error("number", _("This number is already listed."))
            seen.add(normalised)
            if taken_elsewhere(typed, exclude_pk=form.instance.pk):
                form.add_error("number", ALREADY_IN_USE)

    def save(self, commit: bool = True):
        """Save the rows, then settle which of them is the primary.

        The radio names a form prefix rather than a primary key, because a row being added
        for the first time has no key yet and somebody adding their first two numbers has
        to be able to say which is which.
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
            # row becomes it, because a holder with numbers and no primary shows none at
            # all once the feature is switched off again.
            existing = self.instance.phone_numbers.filter(is_primary=True).first()
            wanted = existing or self.instance.phone_numbers.first()
        if wanted is not None:
            set_primary(wanted)
        return saved


def formset_for(holder, *, default_country: str = "", data=None, prefix: str = "phone_numbers"):
    """The rows for one holder, ready to render or to save."""
    factory = generic_forms.generic_inlineformset_factory(
        PhoneNumber,
        form=PhoneNumberForm,
        formset=BasePhoneNumberFormSet,
        extra=1,
        can_delete=True,
    )
    return factory(
        data=data,
        instance=holder,
        prefix=prefix,
        form_kwargs={"default_country": default_country},
    )
