"""What the blocks of rows on a page have in common, whatever the rows hold.

Telephone numbers, links, postal addresses and identifiers are each a formset of their own,
bound to a holder -- a profile, a contact -- and saved with the page they are drawn on. Two
things are true of them and were being remembered, or forgotten, one page at a time: whose a
new row is, and what to make of a row the page still carries after it has gone.
"""

from __future__ import annotations


def owner_of(holder):
    """The account a holder's rows belong to: a contact's owner, a profile's user.

    A number, a link and an address are owned records, and `owner` is in none of their
    forms: nobody types whose a number is. So it is set before the insert, and by the rows
    themselves -- each formset's `save_new` asks this of the holder it is bound to -- so that
    a page which binds the rows to a holder and calls `save()` has done all it needs to. It
    used to be each page's job. The contact form did it in a loop over the forms; *Your
    details* did it for its links and for nothing else, so adding a number or an address
    there failed on `owner_id` and took the whole page with it (#454).

    The two holders there are name their account differently, and that is the whole of the
    rule. It is asked as the row is saved rather than when the formset is built: a page that
    creates its holder builds the rows first and binds them once the holder exists.
    """
    return holder.owner if hasattr(holder, "owner") else holder.user


def leaving(formset) -> set:
    """The keys of the stored rows this submission removes.

    What a block compares a typed value with is the rows that will still be there once it
    is saved. A row with *Remove* ticked will not be, and comparing with it refused the
    one thing a person does in a single save when they have filed a value under the wrong
    row: take it off there and add it here (#461).

    Asked from a formset's ``clean``, once every row has been cleaned.
    """
    return {
        form.instance.pk
        for form in formset.initial_forms
        if form.instance.pk is not None
        and hasattr(form, "cleaned_data")
        and formset._should_delete_form(form)
    }


def remove_first(formset) -> None:
    """Take the rows marked for removal out of the table before any other row is written.

    Django writes the saved rows in the order they are listed, removing or changing each in
    turn, so a row taking the value of one removed further down would reach the table while
    that value was still in it, and a uniqueness rule would refuse a save the block had
    just called valid (#461). Removed here first, they are passed over afterwards the way
    Django passes over any row that has no key -- the order `OneOfEachKind` already writes
    identifiers in, for the same reason (#307).

    Called at the top of a formset's ``save``, when it is committing.
    """
    for form in formset.deleted_forms:
        if form.instance.pk is not None:
            formset.delete_existing(form.instance)


class RowsAlreadyGone:
    """Formset mixin: a saved row that is no longer in the table has already been removed.

    A page is drawn with its rows, and may be saved long after: from a second tab, from the
    copy the Back button brings back, after a row was taken off *Your details* at once
    (#303). The copy still posts the key of a row that has gone. Django looks the key up
    among the formset's own rows, finds nothing, and refuses the hidden field it came in --
    so the whole page was refused, with the error on a field no template draws, and the
    page came back looking exactly as it was sent, as often as it was sent.

    Such a row is treated here as one marked for removal: its errors do not count, nothing
    is compared with what it holds, and saving passes over it, as Django's
    `save_existing_objects` passes over any row whose instance has no key. That is also the
    whole of what happens to a key somebody made up, or to another person's: it names no row
    of this holder, so nothing is read from it and nothing is written.

    Each row carries `already_gone`, which a template reads to draw the row as the key it
    left (`partials/removed_row.html`) rather than as a row with boxes nothing answers to.
    """

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        form.already_gone = (
            self.is_bound and i < self.initial_form_count() and form.instance.pk is None
        )
        return form

    def _should_delete_form(self, form) -> bool:
        return getattr(form, "already_gone", False) or super()._should_delete_form(form)
