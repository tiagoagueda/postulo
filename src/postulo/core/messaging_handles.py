"""Several messaging handles per holder, and the one that counts (#682).

Everything that reads a handle goes through here rather than through the rows directly, for
the reason :mod:`postulo.core.phone_numbers` gives: the primary is the one to use, and the
feature may be off for this person. **Off offers none and keeps every row**: there was no
handle anywhere before this existed, so there is no single box to fall back to, and the
rows stay exactly where they are, waiting to be offered again.

What a service is and which exist is `core.messaging_services`, a registry plugins supply;
what this module does with it is the row -- the choice, the name asked for only under
*Other*, and the check that a handle filed under a service is one of that service's, which
is made where a handle is taken in and never on a row as it is stored.
"""

from __future__ import annotations

from django import forms
from django.contrib.contenttypes import forms as generic_forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from postulo.plugins.messaging_contacts import MESSAGING_CONTACTS

from . import messaging_services
from .formsets import RowsAlreadyGone, leaving, owner_of, remove_first
from .messaging_services import OTHER
from .models import MessagingHandle
from .web_links import ServiceSelect

#: How many handles a holder may keep. Generous, and the bound a file or a request is
#: held to: a holder with thousands is not a person's contact details.
MAX_PER_HOLDER = 50


def several_allowed(person) -> bool:
    """Whether this person is offered messaging handles at all."""
    from postulo.plugins.policy import decide

    return bool(decide(MESSAGING_CONTACTS, person).on)


def primary_for(holder) -> MessagingHandle | None:
    """The handle to use for this holder, or nothing when it has none."""
    return holder.messaging_handles.filter(is_primary=True).first()


def handles_for(holder, person) -> list[MessagingHandle]:
    """What to show for this holder: all of them, or none while the feature is off."""
    if not several_allowed(person):
        return []
    return list(holder.messaging_handles.all())


def kept_back(holder, person) -> int:
    """How many handles are being kept and not shown, because the feature is off.

    Told to the person, so that "switched off" reads as *not offered* rather than as *gone*.
    """
    if several_allowed(person):
        return 0
    return holder.messaging_handles.count()


@transaction.atomic
def set_primary(handle: MessagingHandle) -> None:
    """Make this the holder's primary handle, and the only one.

    Cleared first and set second, because the partial unique index means the
    two-primaries state cannot exist even for the length of a statement.
    """
    MessagingHandle.objects.filter(
        content_type=handle.content_type, object_id=handle.object_id, is_primary=True
    ).exclude(pk=handle.pk).update(is_primary=False)
    if not handle.is_primary:
        handle.is_primary = True
        handle.save(update_fields=["is_primary", "updated_at"])


@transaction.atomic
def ensure_one_primary(holder) -> MessagingHandle | None:
    """The holder keeps the primary it has; failing that, its first handle becomes it.

    What saving the rows does when nobody said which one is the primary, and what taking a
    row off *Your details* at once does afterwards (#303): one rule for both, so they cannot
    disagree about who inherits it.
    """
    mine = holder.messaging_handles.all()
    wanted = mine.filter(is_primary=True).first() or mine.first()
    if wanted is not None:
        set_primary(wanted)
    return wanted


# ------------------------------------------------------ handles taken in without a page


def chosen_in_a_file(row) -> str:
    """What a file's word for a handle's service comes to, as the choice a row would post.

    A file is a claim: written by this instance, by another with plugins this one has not,
    or by hand. What it says is believed only as far as this instance can check it, and
    none of the answers can refuse a handle: a service this instance knows and the handle
    is one of its handles is that service; anything else -- blank, which is how *Other* is
    written, a key not known here, a handle that is not one -- is *Other*.
    """
    said = row.get("service") if isinstance(row, dict) else None
    named = messaging_services.find(said) if isinstance(said, str) else None
    if named is not None and named.accepts(named.normalise(str(row.get("handle") or ""))):
        return named.key
    return OTHER


def read_from_a_file(row) -> dict | None:
    """One handle out of a file, as `add_handles` writes it, or nothing if it is unusable.

    Never raises: a file's row that cannot be kept is passed over, as every other block
    does. A row under *Other* with no name is called by the service key it carried, so a
    handle on a service this instance does not know is kept and not lost.
    """
    if not isinstance(row, dict):
        return None
    handle = " ".join(str(row.get("handle") or "").split())
    if not handle:
        return None
    chosen = chosen_in_a_file(row)
    label = " ".join(str(row.get("label") or "").split())
    if chosen == OTHER and not label:
        label = " ".join(str(row.get("service") or "").split()) or str(_("Other"))
    try:
        service, label, handle = messaging_services.settle(chosen, handle, label[:60])
    except ValidationError:
        return None
    return {
        "service": service,
        "label": label,
        "handle": handle,
        "is_primary": bool(row.get("is_primary")),
    }


def checked_rows(rows) -> list[dict]:
    """Handles handed over from outside a page -- the API -- each checked as a row is.

    A row is a ``handle`` and may say a ``service``, a ``label`` and whether it
    ``is_primary``. The service follows the rule the page does (`messaging_services.settle`).
    What comes back is what `add_handles` writes; the first thing wrong is a
    `ValidationError` naming the handle it is about, and nothing has been written.
    """
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if len(rows) > MAX_PER_HOLDER:
        raise ValidationError(
            _("A contact has at most %(max)s messaging handles.") % {"max": MAX_PER_HOLDER},
            code="too_many",
        )
    for row in rows:
        handle = str(row.get("handle") or "").strip()
        if not handle:
            raise ValidationError(_("A messaging handle needs a handle."), code="handle")
        try:
            service, label, stored = messaging_services.settle(
                str(row.get("service") or ""), handle, str(row.get("label") or "")
            )
        except ValidationError as error:
            raise ValidationError(f"{handle[:80]}: {error.messages[0]}", code=error.code) from error
        folded = (service, MessagingHandle.fold(service, label, stored))
        if folded in seen:
            listed = _("This handle is already listed.")
            raise ValidationError(f"{handle[:80]}: {listed}", code="duplicate")
        seen.add(folded)
        found.append(
            {
                "service": service,
                "label": label[:60],
                "handle": stored,
                "is_primary": bool(row.get("is_primary")),
            }
        )
    return found


@transaction.atomic
def add_handles(holder, owner, rows: list[dict]) -> list[MessagingHandle]:
    """Write the rows `checked_rows` passed, and settle the holder's primary.

    The first row to ask for the primary gets it; where none asks, the holder keeps the
    primary it had, and failing that its first handle becomes it. A handle the holder
    already has is not written twice.
    """
    made: list[MessagingHandle] = []
    wanted = None
    held = {(row.service, row.comparable): row for row in holder.messaging_handles.all()}
    for row in rows:
        key = (row["service"], MessagingHandle.fold(row["service"], row["label"], row["handle"]))
        if key in held:
            if row["is_primary"] and wanted is None:
                wanted = held[key]
            continue
        handle = MessagingHandle.objects.create(
            owner=owner,
            holder=holder,
            service=row["service"],
            label=row["label"],
            handle=row["handle"],
        )
        held[key] = handle
        made.append(handle)
        if row["is_primary"] and wanted is None:
            wanted = handle
    if wanted is not None:
        set_primary(wanted)
    elif made:
        ensure_one_primary(holder)
    return made


# --------------------------------------------------------------- the rows on a page


class MessagingHandleForm(forms.ModelForm):
    """One row: the service it is on, what to call it under *Other*, and the handle.

    ``is_primary`` is not here, for the reason the telephone rows give: the template renders
    one radio per row under a single name outside the formset's prefixes, so the browser
    enforces *exactly one* before anything is posted, and the formset reads the answer.

    **The service is a choice of three sorts**: one of the services this instance offers;
    *Other*, posted as ``other`` and stored as nothing; and, on a row being added, nothing
    chosen at all, which is *Other* too -- a handle is never recognised by its shape.

    **Checked when it changes, and not otherwise.** A row saved again with its handle and
    its service as they were is not checked against a pattern that may have changed since:
    nothing stored is refused in retrospect.
    """

    service = forms.ChoiceField(label=_("Service"), required=False, widget=ServiceSelect)

    class Meta:
        model = MessagingHandle
        fields = ("service", "label", "handle")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["label"].help_text = ""
        # What is typed here is an identifier a service gave, mostly Latin with an @, a
        # colon and dots in it: it takes the direction of what it holds, not of the page.
        self.fields["handle"].widget.attrs["dir"] = "auto"
        offered = messaging_services.registry()
        stored = bool(self.instance.pk)
        choices = [(key, service.label) for key, service in offered.items()]
        choices.append((OTHER, _("Other")))
        if not stored:
            choices.insert(0, ("", "—"))
        field = self.fields["service"]
        field.choices = choices
        field.widget.facts = {
            "": {"data-icon": messaging_services.OTHER_ICON},
            OTHER: {"data-icon": messaging_services.OTHER_ICON},
            **{key: {"data-icon": service.icon_name} for key, service in offered.items()},
        }
        if stored:
            # A row with no service is *Other*, and so is one whose service no installed
            # plugin knows any more; that one keeps its key unless somebody chooses.
            known = self.instance.service in offered
            self.initial["service"] = self.instance.service if known else OTHER

    @property
    def service_icon(self) -> str:
        """The icon of the service the row is on, for the page to draw beside the select:
        the scripts-off path, as a link's row has."""
        facts = self.fields["service"].widget.facts
        return facts.get(self["service"].value() or "", {}).get("data-icon", "")

    def clean(self):
        data = super().clean()
        handle = data.get("handle")
        if not handle or self.has_error("service"):
            return data
        stored = bool(self.instance.pk)
        kept = self.instance.service if stored else ""
        known = messaging_services.find(kept) is not None
        as_drawn = kept if known else OTHER
        chosen = data.get("service") or (as_drawn if stored else "")
        forgotten = bool(kept) and not known
        if stored and chosen == as_drawn and (forgotten or "handle" not in self.changed_data):
            # As it was: a handle and a service nobody touched are not checked again, and
            # a service nothing knows any more is kept for the day it comes back.
            data["service"] = kept
            data["label"] = "" if known else data.get("label", "")
            return data
        try:
            data["service"], data["label"], data["handle"] = messaging_services.settle(
                chosen, handle, data.get("label", "")
            )
        except ValidationError as error:
            beside = {"service": "service", "label": "label"}.get(error.code, "handle")
            self.add_error(beside if beside in self.fields else "handle", error)
        return data


class BaseMessagingHandleFormSet(RowsAlreadyGone, generic_forms.BaseGenericInlineFormSet):
    """The rows together: nothing listed twice for the holder.

    A row the page still carries and the table no longer holds has already been removed
    (`RowsAlreadyGone`).
    """

    def clean(self) -> None:
        super().clean()
        holder = self.instance
        held: dict[int, tuple[str, str]] = {}
        if holder is not None and holder.pk:
            # What will still be listed once this is saved: a row removed in the same save
            # holds nothing, so its handle is free for another row to take (#461).
            staying = holder.messaging_handles.exclude(pk__in=leaving(self))
            held = {pk: (s, c) for pk, s, c in staying.values_list("pk", "service", "comparable")}
        seen: set[tuple[str, str]] = set()
        counted = 0
        for form in self.forms:
            if not form.is_valid() or form.cleaned_data.get("DELETE"):
                continue
            typed = (form.cleaned_data.get("handle") or "").strip()
            if not typed:
                continue
            counted += 1
            key = (
                form.cleaned_data.get("service", ""),
                MessagingHandle.fold(
                    form.cleaned_data.get("service", ""),
                    form.cleaned_data.get("label", ""),
                    typed,
                ),
            )
            # Compared with what the holder's *other* rows hold in the table, and never
            # with what this row's instance says it held (a ModelForm has written the
            # typed value onto it by now).
            elsewhere = any(value == key for pk, value in held.items() if pk != form.instance.pk)
            if key in seen or elsewhere:
                form.add_error("handle", _("This handle is already listed."))
            seen.add(key)
        if counted > MAX_PER_HOLDER:
            raise ValidationError(
                _("There are at most %(max)s messaging handles here.") % {"max": MAX_PER_HOLDER}
            )

    def save_new(self, form, commit=True):
        # Whose the row is, set where the row is made (#454).
        form.instance.owner = owner_of(self.instance)
        return super().save_new(form, commit=commit)

    def save(self, commit: bool = True):
        """Save the rows, then settle which of them is the primary.

        The radio names a form prefix rather than a primary key, because a row being added
        for the first time has no key yet. The rows marked for removal go first, so the
        handle one of them gives up can be taken by another row in the same save (#461).
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
            ensure_one_primary(self.instance)
        return saved


def formset_for(holder, *, data=None, prefix: str = "messaging"):
    """The rows for one holder, ready to render or to save."""
    factory = generic_forms.generic_inlineformset_factory(
        MessagingHandle,
        form=MessagingHandleForm,
        formset=BaseMessagingHandleFormSet,
        extra=1,
        can_delete=True,
        max_num=MAX_PER_HOLDER,
        validate_max=True,
    )
    return factory(data=data, instance=holder, prefix=prefix)
