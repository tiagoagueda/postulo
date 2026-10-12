"""Views that belong to no particular feature."""

from __future__ import annotations

import functools
from pathlib import Path

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from . import arranging, widgets


def home(request: HttpRequest):
    """The landing page for visitors, and the dashboard for anyone signed in.

    The dashboard is built from widgets now (#44). What it shows and in what order is the
    person's own arrangement; what each widget computes is the widget's business, and the
    shared work behind several of them happens once.

    With ``?arrange=1`` the same page is in its editing mode (#201): every widget grows the
    controls to move and remove it, and what is not shown is offered below. A POST here is
    one of those actions, and redirects back into the mode.
    """
    if request.method == "POST":
        return arranging.apply(request)
    if not request.user.is_authenticated:
        return render(request, "core/home.html")

    editing = arranging.wanted(request)
    profile = (
        arranging.profile_for(request.user) if editing else getattr(request.user, "profile", None)
    )
    page = widgets.build_page(request, profile)
    fresh = widgets.new_for(profile)
    context = {
        "page": page,
        # Said here rather than by a widget arriving unannounced: a release or a plugin has
        # something this account has never been offered, and the mode is where it waits
        # (#123).
        "new_widgets": fresh,
        "new_names": ", ".join(widget.called for widget in fresh),
        "arranging": editing,
    }
    if editing:
        context.update(arranging.context_for(profile, page))
    return render(request, "core/dashboard.html", context)


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for container orchestration and uptime monitoring."""
    try:
        connection.ensure_connection()
    except Exception:  # pragma: no cover - only on a broken database
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    from .updates import installed_version

    return JsonResponse({"status": "ok", "database": "ok", "version": installed_version()})


#: The colours an installed window is drawn in: its title bar, and the ground it shows while
#: Postulo loads. The masthead's white and the page's own off-white, from the stylesheet's
#: tokens; the page head says the dark ones (`base.html`), because a manifest has no way to.
THEME_COLOUR = "#ffffff"
BACKGROUND_COLOUR = "#f8fafc"


def manifest(request: HttpRequest) -> JsonResponse:
    """The web app manifest, so a browser installs Postulo as an app of its own (#895).

    Rendered rather than served as a static file because an operator can name their
    instance, and a home screen saying "Postulo" on an instance called something else is
    exactly the sort of small wrongness that makes software feel like somebody else's. It is
    in the language of whoever asks: `base.html` links it with credentials, so the shortcut
    names are the account's, and a browser that sends none gets the instance's default.

    `id` and `scope` are the root, so the app is one thing wherever it was installed from,
    and opens on the board. The icons are two pairs on purpose: a launcher that crops every
    icon to its own shape takes the `maskable` one, which is padded and opaque, and anything
    else takes the plain mark; one image for both is the one that gets cut into.
    """
    from django.templatetags.static import static

    from . import languages, site

    name = site.instance_name()
    code = languages.current()
    response = JsonResponse(
        {
            "id": "/",
            "name": name,
            "short_name": name,
            "description": str(site.tagline() or _("Your job search, your data.")),
            "lang": code,
            "dir": languages.direction(code),
            "start_url": reverse("applications:board"),
            "scope": "/",
            "display": "standalone",
            "background_color": BACKGROUND_COLOUR,
            "theme_color": THEME_COLOUR,
            "categories": ["productivity", "business"],
            "icons": [
                {"src": static("brand/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                {"src": static("brand/icon-512.png"), "sizes": "512x512", "type": "image/png"},
                {
                    "src": static("brand/icon-maskable-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
            "shortcuts": [
                {"name": str(label), "url": reverse(url)}
                for label, url in (
                    (_("Board"), "applications:board"),
                    (_("Listings"), "listings:list"),
                    (_("Calendar"), "applications:calendar"),
                )
            ],
        },
        content_type="application/manifest+json",
    )
    response["Content-Language"] = code
    return response


def offline(request: HttpRequest) -> HttpResponse:
    """What the installed app shows when it cannot reach its server (#895).

    The service worker fetches this page once, keeps it, and answers a page load with it
    when the network gives nothing at all. It is the one thing the worker keeps: no
    application page and no data, so nothing personal is ever read from a cache. It names
    nobody and carries nothing of the account but its language, and it needs no sign-in,
    because the worker that asks may be refreshing it for a visitor.

    Self-contained, because when it is shown nothing else can be fetched: its rules are the
    500 page's stylesheet, inlined under the request's nonce rather than linked. The worker
    keeps the response whole, headers and all, so the nonce in its policy is the nonce on
    its `<style>`.
    """
    from django.middleware.csp import get_nonce

    from . import languages, site

    code = languages.current()
    nonce = get_nonce(request)
    response = render(
        request,
        "offline.html",
        {
            "instance_name": site.instance_name(),
            "styles": _offline_styles(),
            "csp_nonce": str(nonce) if nonce is not None else "",
        },
    )
    response["Content-Language"] = code
    # Its own answer about caching, so the private-page rule does not mark it `no-store`:
    # not because a cache may keep it -- only the worker does, deliberately -- but because
    # it holds nothing that rule protects.
    response["Cache-Control"] = "no-cache"
    return response


@functools.cache
def _offline_styles() -> str:
    from django.contrib.staticfiles import finders

    found = finders.find("css/error.css")
    return Path(found).read_text(encoding="utf-8") if found else ""


def server_error(request: HttpRequest):
    """The 500 page, in the language the request was being read in (#421).

    Django's own view renders the template with no context, which leaves the page to name
    a language and a direction of its own. This one says them, and nothing else: no
    context processor runs, because the database may be the very thing that failed.
    """
    from django.http import HttpResponseServerError
    from django.template import loader

    from . import languages

    code = languages.current()
    body = loader.render_to_string(
        "500.html", {"LANGUAGE_CODE": code, "text_direction": languages.direction(code)}
    )
    return HttpResponseServerError(body)
