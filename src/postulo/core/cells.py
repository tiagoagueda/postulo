"""Changing a value where it is shown, and where the refusal goes when it is refused.

> something like dispatcharr implemented on their channel listigs

Every edit in Postulo is a page. That is fine for a company nobody looks at twice and
tiring for a list of forty, where the thing wanted is one word in one cell — and it is the
feature that makes a long table feel like a spreadsheet rather than a directory of forms
(#135).

It also asks a question the rest of this application has never had to answer. **A form has
somewhere to put a refusal**: under a labelled field, in a form with a heading, which is the
arrangement ``aria-describedby`` and ``aria-invalid`` were wired for. A cell four columns
wide has nowhere. Under it breaks the row; a toast is gone before a screen reader reaches
it; a tooltip is not announced at all.

**The answer here, decided once rather than improvised per column.** The cell that is being
edited *is* a small form — a label, an input, and room for a message — so the refusal goes
exactly where every other refusal in Postulo goes, in the markup Postulo already uses for
one. The row is taller while it is being edited, which it already was, because it is holding
an input.

That partial turned out to be the whole answer to the second half of the question. It ties
the message to the input by ``aria-describedby``, and it already carries ``role="alert"`` —
with a comment saying the role earns its place *through htmx*, because an error inserted
into a live page is what the role is for. A cell arriving through a swap is precisely that,
so the refusal is announced by the machinery that was already there. The only thing missing
was ``aria-invalid`` on the input, which the view sets.

**Every editable cell posts to the form the page already uses.** `docs/PLAN.md` and
`CLAUDE.md` both say it: status changes go through `change_status`, records are written with
`record_event`, fields are never poked. A cell that wrote ``company.name = x; save()`` would
be a second way of saving that has to be kept in step with the first, and the day it drifts
is the day something is saved without its rules. So the machinery here builds a one-field
version of the page's own form and validates through that: every ``clean_`` method, every
uniqueness check, every message, unchanged.

**Not every column can be edited, and the ones that cannot must not look editable.**
`Column.editable` names the form field, and a column that names none draws a value and
nothing else. A count is not editable because it is a count; a date read off a posting is
not editable because it belongs to the posting; a status is not editable *here* because it
goes through a service that writes a timeline entry, and offering a cell that skipped that
would be worse than offering nothing.

**Two tabs, both editing.** The editor carries the row's ``updated_at``, and a save whose
stamp has moved is refused rather than applied. Last-write-wins with nobody told is the
outcome that is hardest to notice and hardest to undo; being told is cheap.

**With no script the cell is a link to the form.** That is a complete fallback rather than a
degraded one: it is exactly what the table did before this existed.
"""

from __future__ import annotations

from django.forms import modelform_factory
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from django.views import View

#: What a cell says when somebody else has changed the row since it was drawn.
STALE = _("Somebody else changed this while you were editing. This is what it says now.")


class EditableCellView(View):
    """One cell: drawn, edited, saved, or refused.

    Subclasses say which model, which form and which template; everything else is here so
    that the second table to want this writes three attributes rather than a view.
    """

    #: The model the row is a row of.
    model = None
    #: The form the *page* uses. One field of it is what a cell edits.
    form_class = None
    #: The partial that draws one cell, given ``object``, ``column`` and ``form``.
    template_name = "partials/table/cell.html"
    #: The table's columns, so a column that is not editable cannot be edited by address.
    columns: tuple = ()
    #: The named route to the page this value is otherwise edited on.
    form_url_name = ""

    def get_queryset(self):
        return self.model.objects.for_user(self.request.user)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

    def column_for(self, key: str):
        """The column being edited, or 404.

        By key against the table's own declaration, so *this column is editable* is decided
        in one place and cannot be worked around by typing an address.
        """
        for column in self.columns:
            if column.key == key and column.editable:
                return column
        raise Http404("That column cannot be edited.")

    def form_for(self, instance, column, data=None):
        """The page's own form, narrowed to one field.

        `modelform_factory` keeps the base form's ``clean_`` methods and its widgets, so a
        cell refuses exactly what the page refuses, in the same words.
        """
        narrowed = modelform_factory(self.model, form=self.form_class, fields=[column.editable])
        return narrowed(data=data, instance=instance, user=self.request.user)

    def form_url(self, obj) -> str:
        """Where the cell points when there is no script to open an editor.

        The page the whole value is edited on, which is what the table linked to before any
        of this existed -- so *scripts off* degrades to exactly today rather than to nothing.
        """
        return reverse(self.form_url_name, args=[obj.pk])

    def render(self, request, obj, column, form=None, editing=False, message=""):
        """The cell, however it currently is.

        The bound field and the value are worked out here rather than in the template: a
        template filter that reaches a field by a name held in a variable is a filter to
        write, to test and to explain, and the view already has both to hand.
        """
        field = form[column.editable] if form is not None else None
        if field is not None and field.errors:
            # `aria-describedby` comes from Django; this is the other half of the pair, and
            # it is what tells somebody the value they are looking at was refused.
            field.field.widget.attrs["aria-invalid"] = "true"
        return render(
            request,
            self.template_name,
            {
                "object": obj,
                "column": column,
                "field": field,
                "value": getattr(obj, column.editable, ""),
                "editing": editing,
                "message": message,
                "stamp": obj.updated_at.isoformat() if hasattr(obj, "updated_at") else "",
                "cell_url": request.path,
                "form_url": self.form_url(obj),
            },
        )

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """The editor, or the value back again when the request says to abandon."""
        obj = self.get_object()
        column = self.column_for(kwargs["column"])
        if request.GET.get("abandon"):
            return self.render(request, obj, column)
        return self.render(request, obj, column, form=self.form_for(obj, column), editing=True)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        obj = self.get_object()
        column = self.column_for(kwargs["column"])

        if self.moved_on(obj, request.POST.get("stamp", "")):
            obj.refresh_from_db()
            return self.render(request, obj, column, message=str(STALE))

        form = self.form_for(obj, column, data=request.POST)
        if not form.is_valid():
            return self.render(request, obj, column, form=form, editing=True)
        form.save()
        return self.render(request, obj, column)

    @staticmethod
    def moved_on(obj, stamp: str) -> bool:
        """Whether the row changed since the editor was drawn.

        A missing or unreadable stamp is treated as *not moved*: refusing every save because
        a browser sent something odd would be a worse failure than the one this prevents.
        """
        held = getattr(obj, "updated_at", None)
        seen = parse_datetime(stamp) if stamp else None
        return bool(held and seen and held.replace(microsecond=0) > seen.replace(microsecond=0))
