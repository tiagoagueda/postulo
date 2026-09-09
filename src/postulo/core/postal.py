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

from django import forms
from django.contrib.contenttypes.forms import (
    BaseGenericInlineFormSet,
    generic_inlineformset_factory,
)
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.translation import gettext_lazy as _

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
def ensure_one_primary(holder) -> None:
    """After a removal, make sure something is primary if anything is left.

    A holder with addresses and no primary is a holder whose letter has no address on it,
    and that state is reachable by deleting the primary one. The oldest remaining takes it,
    which is the same rule the ordering already implies.
    """
    addresses = for_holder(holder)
    if not addresses.exists() or addresses.filter(is_primary=True).exists():
        return
    first = addresses.order_by("created_at", "pk").first()
    first.is_primary = True
    first.save(update_fields=["is_primary", "updated_at"])


def location_line(holder) -> str:
    """A town and a country, from the primary address, for `Profile.location` to default to.

    **Never the street.** Guidance across most of Europe is that a CV carries a city and a
    country, partly because the street is irrelevant to the reader and partly because a
    precise address invites them to draw conclusions about somebody from where they live.
    `Profile.location` stays somebody's own overridable line; this is only what it starts
    from (#92).
    """
    from postulo.core import phones

    address = primary_for(holder)
    if address is None:
        return ""
    parts = [address.municipality.strip(), phones.country_name(address.country)]
    return ", ".join(part for part in parts if part)


# --------------------------------------------------------------- the rows on a page


class PostalAddressForm(forms.ModelForm):
    """One row: what kind of address it is, and its parts.

    ``is_primary`` is deliberately not here, for the reason `phone_numbers.py` gives: a
    checkbox per row can be ticked twice, and the invariant would then be something the
    formset repairs after the fact. The template renders one radio per row under a single
    name outside the formset's prefixes, so the browser enforces *exactly one*.

    The labels here are the neutral ones. What a region is *called* depends on the address's
    country rather than on the reader's language — *State*, *Distrito*, *Prefecture* — and
    saying it properly is #147's job.
    """

    class Meta:
        model = PostalAddress
        fields = ("kind", "label", "street", "postcode", "municipality", "region", "country")

    def __init__(self, *args, default_country: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        from postulo.core import phones

        # Two rows, because a street address is two lines in plenty of places and one box
        # of one line quietly asks somebody to leave the second out.
        self.fields["street"].widget = forms.Textarea(attrs={"rows": 2})

        self.fields["country"] = forms.ChoiceField(
            label=_("country"),
            required=False,
            choices=[("", "—"), *phones.country_choices()],
        )
        if not self.instance.pk and default_country:
            self.fields["country"].initial = default_country
        for name in ("street", "postcode", "municipality", "region"):
            self.fields[name].required = False

    def clean(self):
        data = super().clean()
        if data.get("kind") == PostalAddress.Kind.OTHER and not (data.get("label") or "").strip():
            self.add_error("label", _("Say what this address is."))
        return data


class BasePostalAddressFormSet(BaseGenericInlineFormSet):
    """The rows together: nothing listed twice **by this person**.

    Only by this person. Addresses are not unique across the instance and must not be: two
    people at one address is a household, and refusing the second would also disclose that
    somebody else on this server lives there (#92).
    """

    def clean(self) -> None:
        super().clean()
        seen: set[str] = set()
        for form in self.forms:
            if not form.is_valid() or form.cleaned_data.get("DELETE"):
                continue
            comparable = form.instance.comparable_form()
            if not comparable:
                continue
            if comparable in seen:
                form.add_error(None, _("This address is already listed."))
            seen.add(comparable)

    def save(self, commit: bool = True):
        """Save the rows, then settle which of them is the primary.

        The radio names a form prefix rather than a primary key, because a row being added
        for the first time has no key yet.
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


def formset_for(holder, *, default_country: str = "", data=None, prefix: str = "addresses"):
    """The rows for one holder, ready to render or to save."""
    factory = generic_inlineformset_factory(
        PostalAddress,
        form=PostalAddressForm,
        formset=BasePostalAddressFormSet,
        extra=1,
        can_delete=True,
    )
    return factory(
        data=data,
        instance=holder,
        prefix=prefix,
        form_kwargs={"default_country": default_country},
    )
