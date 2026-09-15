"""Saving how a person likes a table laid out."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import tables
from .redirects import safe_next


@login_required
@require_POST
def table_settings(request: HttpRequest, name: str) -> HttpResponse:
    """Store the *Columns* choices for one table, or reset them, then go back."""
    table = tables.TABLES.get(name)
    if table is None:
        raise Http404
    if "reset" in request.POST:
        tables.save_settings(request.user, name, None)
        messages.success(request, _("Back to the usual columns."))
    elif "shape" in request.POST:
        # The shape switch on the applications page (#102): one field, remembered beside
        # the columns, and back to exactly where the person was -- filters, search and
        # sort included, since they travel in `next`. Nothing said: the page shows it.
        shape = request.POST.get("shape", "")
        if shape in table.shapes:
            current = tables.settings_for(request.user, name)
            tables.save_settings(request.user, name, {**current, "shape": shape})
    else:
        current = tables.settings_for(request.user, name)
        tables.save_settings(request.user, name, table.clean_settings(request.POST, current))
        if "move" not in request.POST:
            messages.success(request, _("Columns saved."))

    return redirect(safe_next(request, "/"))
