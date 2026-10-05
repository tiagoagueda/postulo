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
        # Everything the person set on the table goes, shape included, but the saved views
        # they built and named are not what this button says it undoes (#503).
        current = tables.settings_for(request.user, name)
        kept = {"views": current["views"]} if isinstance(current.get("views"), list) else None
        tables.save_settings(request.user, name, kept)
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
        if "width" in request.POST:
            # A drag or an arrow press on a column's handle (#136) saves in the background:
            # nothing to render and nothing to say, so the script gets no redirect to follow
            # and no message is left queued for the next page (#521).
            return HttpResponse(status=204)
        if "move" not in request.POST:
            messages.success(request, _("Columns saved."))

    return redirect(safe_next(request, "/"))


@login_required
@require_POST
def table_views(request: HttpRequest, name: str) -> HttpResponse:
    """Keep, forget, or make default a saved view of one table, then go back (#259).

    A view is saved from the address the person is looking at: `next` is that address and
    its query string is the view. Nothing is read out of the form but the name, so the view
    can only ever hold what the page already showed.
    """
    from urllib.parse import urlsplit

    table = tables.TABLES.get(name)
    if table is None:
        raise Http404
    current = tables.settings_for(request.user, name)
    action = request.POST.get("action", "save")
    slug = request.POST.get("slug", "")
    back = safe_next(request, "/")
    if action == "save":
        title = request.POST.get("name", "")
        clean, view_slug = table.clean_view_name(title)
        if not view_slug:
            if clean:
                messages.error(request, _("A view's name needs at least one letter or digit."))
            else:
                messages.error(request, _("A view needs a name."))
        else:
            saved = table.save_view(
                current,
                title,
                urlsplit(back).query,
                table(request, current).visible_keys_in_order,
            )
            tables.save_settings(request.user, name, saved)
            messages.success(request, _("View saved."))
            # Back to the view under its own name, so what was saved is what is shown.
            view = next(v for v in table._views_of(saved) if v.slug == view_slug)
            return redirect(table(request, saved).view_url(view, path=urlsplit(back).path))
    elif action == "delete":
        tables.save_settings(request.user, name, table.forget_view(current, slug))
        messages.success(request, _("View forgotten."))
    elif action == "default":
        tables.save_settings(request.user, name, table.make_default(current, slug))
        messages.success(
            request,
            _("This is what the table opens as now.")
            if slug and any(v.slug == slug for v in table._views_of(current))
            else _("The table opens plain again."),
        )
    return redirect(back)
