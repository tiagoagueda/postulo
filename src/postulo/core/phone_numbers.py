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
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from postulo.plugins.phone_numbers import PHONE_NUMBERS

from . import phone_field, phones
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


def mine(person) -> models.QuerySet[PhoneNumber]:
    """The numbers belonging to this person's own profile, and nothing else.

    `PhoneNumber` has a generic holder, so one row belongs to a profile and the next to a
    contact at a company — both owned by the same account. "My numbers" is therefore a query
    and not a field, and anything touching recovery has to ask it that way: a recruiter's
    switchboard is a number this account *owns* and never a number this account *is*.
    """
    from django.contrib.contenttypes.models import ContentType

    profile = getattr(person, "profile", None)
    if profile is None or profile.pk is None:
        return PhoneNumber.objects.none()
    return PhoneNumber.objects.filter(
        content_type=ContentType.objects.get_for_model(type(profile)),
        object_id=profile.pk,
    )


def recovery_candidates(person) -> list[PhoneNumber]:
    """This person's numbers that *could* be made the way back into their account.

    Two conditions. The number is one of *theirs*, not one they merely recorded. And it was
    proved recently enough to still describe somebody who answers there — which today means
    none of them are, because nothing can send to a number yet (#143). An empty list on every
    instance is the correct answer rather than a placeholder.
    """
    return [row for row in mine(person) if row.is_verified]


def recovery_number(person) -> PhoneNumber | None:
    """The number that would get this person back in, or nothing.

    **Read regardless of the `phone-numbers` feature being on for them**, and that is the
    boundary this draws (#144). Recovery is instance policy; a feature plugin is a per-person
    preference. A per-person switch that could silently decide whether an account is
    recoverable is the wrong shape — an administrator switching a feature off for somebody
    would quietly remove their way back in, which is the failure the transport interlock
    exists to prevent, arriving through a different door.

    So the plugin governs what is *shown and used*, exactly as it always has, and never
    whether somebody can get back into their account.
    """
    chosen = mine(person).filter(is_recovery=True).first()
    return chosen if chosen is not None and chosen.is_verified else None


def can_get_back_in(person) -> bool:
    """Whether a telephone number is a way back in for this person, today."""
    return recovery_number(person) is not None


def accounts_without_a_recovery_number() -> int:
    """Active accounts that a text message could not get back into.

    Counted rather than assumed, exactly as the passkey count is: on every instance today
    this is every account, because nothing can confirm a number yet, and the day that stops
    being true it stops being true here without anything else changing.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone

    from postulo.accounts.models import Profile
    from postulo.core.models import PhoneNumber

    fresh_enough = timezone.now() - PhoneNumber.VERIFICATION_LASTS
    with_one = PhoneNumber.objects.filter(
        content_type=ContentType.objects.get_for_model(Profile),
        is_recovery=True,
        verified_at__gte=fresh_enough,
    ).values_list("owner_id", flat=True)
    return get_user_model().objects.filter(is_active=True).exclude(pk__in=with_one).count()


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


#: What somebody is told once they have been given that answer too many times in an hour.
#: Honest about what happened rather than vague about it: a message that pretended the save
#: failed for some other reason would be a lie, and would still refuse the save, so it would
#: disclose the same thing while sounding evasive.
ASKED_TOO_OFTEN = _(
    "You have been told about a lot of numbers already recorded here. That answer is "
    "available again in %(minutes)d minutes."
)


def collision_message(person) -> str:
    """The sentence for a number that is already here, while this account may still have it.

    The disclosure itself was decided in #90 and cannot be avoided: instance-wide uniqueness
    cannot be enforced without telling whoever typed a number that somebody else may hold it.
    What changes once a number identifies an account is the *rate* — the same honest sentence,
    asked five hundred times, is a list of which numbers have accounts here, which is the
    shape of email enumeration and wants the same care (#142).

    So the answer is bounded rather than blurred. Somebody editing their own numbers never
    meets the limit; somebody sweeping a numbering range meets it within a minute.
    """
    from . import throttle

    try:
        collision_noticed(person)
    except throttle.TooOften as too_often:
        return str(ASKED_TOO_OFTEN % {"minutes": max(1, round(too_often.retry_after / 60))})
    return str(ALREADY_IN_USE)


def collision_noticed(person) -> None:
    """Spend one of this account's chances to learn that a number is already here.

    The uniqueness rule cannot be enforced without telling whoever typed a number that
    somebody else may hold it — that disclosure was decided in #90 and written down. What
    changes once a number identifies an account is the *rate*: the same answer, asked five
    hundred times, is a list of which numbers have accounts here, which is the shape of email
    enumeration and wants the same care (#142).

    Only the informative answer is charged for. Somebody editing their own numbers never
    meets this; somebody sweeping a range meets it on every question worth asking.
    """
    from . import throttle

    throttle.consume("number-collision", person, throttle.rate_for("POSTULO_NUMBER_RATE"))


def can_be_chosen_here() -> bool:
    """Whether nominating a number is a thing this instance can offer at all.

    Postulo ships no way to send to a telephone, so on almost every instance the answer is
    no — and a control nobody can use, beside a promise nobody can keep, is worse than no
    control. It appears by itself on an instance whose operator installs a gateway (#143).
    """
    from . import channels

    return channels.confirmable(channels.TelephoneChannel())


@transaction.atomic
def set_recovery(number: PhoneNumber | None, *, owner) -> None:
    """Make this the account's way back in, and the only one.

    Cleared first and set second, because the partial unique index means the two-of-them
    state cannot exist even for the length of a statement — the same shape `set_primary`
    has, for a flag with more riding on it.
    """
    PhoneNumber.objects.filter(owner=owner, is_recovery=True).exclude(
        pk=getattr(number, "pk", None)
    ).update(is_recovery=False)
    if number is None or number.is_recovery:
        return
    number.is_recovery = True
    # The model's own refusals, not the form's: this is reached from a shell and an import
    # test as well as from the page.
    number.clean()
    number.save(update_fields=["is_recovery", "updated_at"])


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

    #: Who is answering, for the collision limit. Set by `formset_for`; `None` where a
    #: formset is built directly, in which case the limit has nobody to charge and the
    #: honest sentence is given.
    asked_by = None

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
                form.add_error("number", collision_message(self.asked_by))

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
        self._settle_the_recovery_number()
        return saved

    def _settle_the_recovery_number(self) -> None:
        """Which number gets this account back in, if the instance can offer the choice.

        Kept apart from the primary on purpose (#144): the primary is what a document prints
        and a recruiter dials, and coupling the two would mean changing the number on a CV
        silently changed a way back into the account.

        Nothing is chosen by default, and an empty answer clears it rather than falling back
        to something — a route somebody did not ask for is not one they will remember having.
        """
        if not can_be_chosen_here():
            return
        named = (self.data.get(f"{self.prefix}-recovery") or "").strip()
        wanted = None
        for form in self.forms:
            if form.prefix == named and form.instance.pk and not form.cleaned_data.get("DELETE"):
                wanted = form.instance
                break
        if wanted is not None and not wanted.is_verified:
            # Refused rather than ignored: the person picked a number nobody has answered
            # on, and silently choosing nothing would leave them believing they had a route.
            self.non_form_errors().append(
                str(_("That number has to be confirmed before it can get you back in."))
            )
            return
        # The account, not the holder: the holder is a profile and the flag belongs to
        # whoever owns the row.
        set_recovery(wanted, owner=self.asked_by)


def formset_for(
    holder,
    *,
    default_country: str = "",
    data=None,
    prefix: str = "phone_numbers",
    asked_by=None,
):
    """The rows for one holder, ready to render or to save.

    `asked_by` is who is answering the form, which the collision limit is keyed on. It is the
    account rather than the holder because a holder may be a contact at a company, and the
    thing being bounded is how many numbers *one person* can ask about.
    """
    factory = generic_forms.generic_inlineformset_factory(
        PhoneNumber,
        form=PhoneNumberForm,
        formset=BasePhoneNumberFormSet,
        extra=1,
        can_delete=True,
    )
    formset = factory(
        data=data,
        instance=holder,
        prefix=prefix,
        form_kwargs={"default_country": default_country},
    )
    formset.asked_by = asked_by
    # Read by the template, so the choice appears the day an operator installs a gateway
    # and never before it (#144).
    formset.recovery_can_be_chosen = can_be_chosen_here()
    return formset
