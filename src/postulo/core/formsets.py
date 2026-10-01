"""What a block of rows does with a copy of the page that is out of date."""

from __future__ import annotations


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
