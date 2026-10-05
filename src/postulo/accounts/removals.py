"""Taking one row off *Your details* at once, after asking (#303).

The page used to be one form with one *Save*: a row was removed by ticking its box and saving
everything else with it. A telephone number, a link, an address or an identifier now goes the
moment its dialog is confirmed, which is a different contract, and this module is its terms.

**Each row has its own address**, one per kind of block, and each asks the same questions:
whose row it is (the profile's own, so anybody else's -- and a contact's, which is on another
page -- is a 404), whether the block is on the page at all (a feature switched off offers no
rows, and switching one off deletes nothing, so neither does this), whether the row may go (the
number that gets its owner back in may not), and who inherits the primary. That last answer
is the one saving the rows gives, from the same function, so the two ways of losing a primary
cannot disagree about who gets it.

**What the page draws comes from here as well**: the words a row is called by -- the Remove
button's name, the dialog's heading, the sentence said once it has gone -- and the dialog's id,
so the button in the row and the dialog after the form cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext as _

from postulo.core import messaging_handles, phone_numbers, phones, postal, web_links
from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress, WebLink

from .models import PersonIdentifier


@dataclass(frozen=True)
class Kind:
    """One kind of row on *Your details*, and the block it sits in."""

    #: The address's own name, `accounts:remove_<name>`, and the dialog's id prefix.
    name: str
    model: type[models.Model]
    #: Whether one row of the block is its primary. Identifiers have none.
    has_primary: bool = True


NUMBER = Kind("number", PhoneNumber)
LINK = Kind("link", WebLink)
ADDRESS = Kind("address", PostalAddress)
MESSAGING = Kind("messaging", MessagingHandle)
IDENTIFIER = Kind("identifier", PersonIdentifier, has_primary=False)


# ------------------------------------------------------------------ whose rows these are


def rows(kind: Kind, person) -> models.QuerySet:
    """The rows of this kind on this person's *Your details*, and nothing else.

    The profile's own, never merely the account's: a contact's number is a row this account
    owns, and it is removed on the contact's page, where the form still saves at its foot.
    """
    profile = getattr(person, "profile", None)
    if profile is None or profile.pk is None:
        return kind.model._default_manager.none()
    if kind is IDENTIFIER:
        return PersonIdentifier.objects.filter(profile=profile)
    return kind.model._default_manager.filter(
        owner=person,
        content_type=ContentType.objects.get_for_model(type(profile)),
        object_id=profile.pk,
    )


def offered(kind: Kind, person, row) -> bool:
    """Whether the block this row sits in is on the page at all.

    A feature switched off shows the primary in one box and keeps the rest out of sight,
    untouched; a removal reaching one of those would be the switch deleting something after
    all, so the address answers as though the row did not exist.
    """
    if kind is NUMBER:
        return phone_numbers.several_allowed(person)
    if kind is LINK:
        return web_links.several_allowed(person, row.kind)
    if kind is MESSAGING:
        return messaging_handles.several_allowed(person)
    return True


def anchor(kind: Kind, row) -> str:
    """The id of the block the row sits in, which the page is sent back to."""
    if kind is NUMBER:
        return "section-phones"
    if kind is LINK:
        return f"section-links-{row.kind}"
    if kind is ADDRESS:
        return "section-addresses"
    if kind is MESSAGING:
        return "section-messaging"
    return "section-identifiers"


def words(kind: Kind, row) -> str:
    """What the row is called by: the value somebody would recognise it by.

    The number spaced as it is read back, a link by the name it was given or by its
    address, an address on one line, an identifier by its kind and value.

    A link nobody named is called by its whole address, less the scheme, and not by its
    host, which is what a document prints for it: two repositories on one forge share a
    host, and their bins, their dialogs and the sentence said afterwards would then be word
    for word the same, with the dialog drawn over the one box that tells them apart.
    """
    if kind is NUMBER:
        return phones.readable(row.number)
    if kind is LINK:
        return row.label or row.url.partition("://")[2] or row.url
    if kind is ADDRESS:
        return str(row)
    if kind is MESSAGING:
        return row.display
    return f"{row.display_label} {row.value}"


#: The two characters that keep a quoted value's direction to itself. Written as escapes:
#: neither draws anything, and a line holding one looks like a line without it.
FIRST_STRONG_ISOLATE = "\u2068"
POP_DIRECTIONAL_ISOLATE = "\u2069"


def quoted(value: str) -> str:
    """A row's words as they sit inside a sentence: isolated from the direction around them.

    The sentence is the reader's language and the value is whatever somebody typed. In a
    right-to-left sentence a telephone number's groups are otherwise laid out last group
    first -- "671 345 912 351+" -- and a Latin name pulls the full stop to its wrong side.
    The dialog's heading holds the value in a `<bdi>`; a sentence that travels as plain
    text, to a live region or into a message, takes the same isolation as two characters,
    FIRST STRONG ISOLATE and POP DIRECTIONAL ISOLATE, which draw nothing and say nothing.
    """
    return f"{FIRST_STRONG_ISOLATE}{value}{POP_DIRECTIONAL_ISOLATE}"


def said(value: str) -> str:
    """What is said once a row has gone, beside its block or as the page's message."""
    return _("%(what)s removed.") % {"what": quoted(value)}


def already_gone(value: str) -> str:
    """What is said when a row turns out to have gone before its dialog was answered.

    A copy of the page that is out of date -- a second tab, or the one the Back button
    brings back -- still draws a row taken off somewhere else. Its address answers that
    there is no such row, and `app.js` takes it off that copy too, with this.
    """
    return _("%(what)s had already been removed.") % {"what": quoted(value)}


def refusal(kind: Kind, row) -> str:
    """Why this row may not be taken off, or an empty string when it may."""
    if kind is NUMBER and phone_numbers.gets_back_in(row):
        return _(
            "This number is how you get back into your account, so it cannot be removed. "
            "Choose another number to get you back in first."
        )
    return ""


def hand_on(kind: Kind, row, holder):
    """After ``row`` has gone: who is primary in its block now, by the rule saving follows.

    Nothing for identifiers, which have no primary.
    """
    if kind is NUMBER:
        return phone_numbers.ensure_one_primary(holder)
    if kind is LINK:
        return web_links.ensure_one_primary(holder, row.kind)
    if kind is ADDRESS:
        return postal.ensure_one_primary(holder)
    if kind is MESSAGING:
        return messaging_handles.ensure_one_primary(holder)
    return None


def left(kind: Kind, row, person) -> int:
    """How many rows the block holds now, for the count beside it in the sidebar."""
    remaining = rows(kind, person)
    if kind is LINK:
        remaining = remaining.filter(kind=row.kind)
    return remaining.count()


# --------------------------------------------------------------------- what the page draws


@dataclass(frozen=True)
class Removal:
    """One saved row as *Your details* offers to take it off: the button and its dialog."""

    kind: Kind
    pk: int
    #: The row in words, for the button's name and the dialog's heading.
    value: str
    #: Whether taking it off hands the primary to another row, which the dialog says. True
    #: as the page is drawn; `app.js` keeps the sentence true as rows go without a reload.
    hands_on: bool

    @property
    def already_gone(self) -> str:
        """The sentence for a copy of the page that still draws the row once it has gone."""
        return already_gone(self.value)

    @property
    def dialog(self) -> str:
        """The dialog's id, and the button's `popovertarget`. The row's own key rather than
        a random one: the button is drawn in the row and the dialog after the form, because
        a form cannot hold another, and both have to arrive at the same name."""
        return f"remove-{self.kind.name}-{self.pk}"

    @property
    def action(self) -> str:
        return reverse(f"accounts:remove_{self.kind.name}", args=[self.pk])


def offer(kind: Kind, formset) -> list[Removal]:
    """Mark each saved row of a block with what taking it off would be, and list them.

    Read from the table rather than from the forms: after a save that was refused, a form's
    instance carries what was typed into it, and the dialog is about the row as it is kept.
    A row already taken off by a removal -- posted back as its key and its removal -- has no
    instance any more and is offered nothing.
    """
    saved = [form for form in formset.initial_forms if form.instance.pk]
    kept = kind.model._default_manager.in_bulk([form.instance.pk for form in saved])
    found: list[Removal] = []
    for form in saved:
        row = kept.get(form.instance.pk)
        if row is None:
            continue
        removal = Removal(
            kind=kind,
            pk=row.pk,
            value=words(kind, row),
            hands_on=bool(getattr(row, "is_primary", False)) and len(saved) > 1,
        )
        form.removal = removal
        found.append(removal)
    return found


def saved_count(formset) -> int:
    """How many rows a block holds, as the sidebar counts them.

    `initial_form_count()` is the table's answer on a fresh page and the posted answer on
    one sent back with an error -- which, since rows can go at once, may include a row that
    has already gone.
    """
    return sum(1 for form in formset.initial_forms if form.instance.pk)
