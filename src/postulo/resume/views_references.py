"""Publications as a bibliography file, out and in (#879).

One page, as *Places as GeoJSON* is: the downloads on it, and a file read in. A file is read
and what was read is held in the session (parsed, capped, never the file), the page lists each
entry with the ones the person already holds marked and **none ticked**, and nothing reaches
the career record until somebody ticks an entry and presses the button. The mapping and the
limits are `references`; this is the page.

**Typing DOIs** is the online half. It is offered only while *Bibliography lookups* is on,
which is off until an administrator says so, and which needs *Publication lookups*. Each DOI
is one request through `lookups.from_doi`, against the same allowance as *Look up*, on a
press and never otherwise; what comes back joins the same list and is reviewed the same way.
"""

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.generic import TemplateView

from postulo.core import modules, throttle

from . import lookups, ordering, publications, references
from .models import Publication

#: How many DOIs one press may ask about: each is a request to doi.org.
MAX_DOIS = 10
LOOKUPS = "bibliography-lookups"


def lookups_offered(user) -> bool:
    return modules.is_on(user, LOOKUPS)


def clean_entry(values: dict, taken: set[str]) -> dict[str, str]:
    """What is held, held again to the rules a person's own typing meets, and the columns."""
    kept: dict[str, str] = {}
    for name in references.FIELDS:
        value = values.get(name)
        if not isinstance(value, str):
            continue
        limit = Publication._meta.get_field(name).max_length if name != "entry_type" else 20
        kept[name] = (
            references.text(value, limit or 200)
            if name not in ("authors", "editors")
            else ("\n".join(references.text(line, 200) for line in value.splitlines())[:limit])
        )
    return publications.sanitise(kept, taken)


class ReferencesView(LoginRequiredMixin, TemplateView):
    template_name = "resume/references.html"

    # ------------------------------------------------------------------ downloading

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        wanted = request.GET.get("download")
        if wanted:
            return self.download(request, wanted)
        return super().get(request, *args, **kwargs)

    def chosen_publication(self, request: HttpRequest):
        """The one publication ``?publication=`` names, if it is the person's; else a 404."""
        pk = request.GET.get("publication", "")
        if not pk:
            return None
        if not pk.isdecimal() or len(pk) > 12:
            raise Http404
        try:
            return Publication.objects.for_user(request.user).get(pk=int(pk))
        except Publication.DoesNotExist:
            raise Http404 from None

    def download(self, request: HttpRequest, key: str) -> HttpResponse:
        fmt = references.FORMATS.get(key)
        if fmt is None:
            raise Http404
        one = self.chosen_publication(request)
        items = (
            [one]
            if one is not None
            else list(Publication.objects.for_user(request.user).order_by("order", "pk"))
        )
        body = references.write(fmt.key, items)
        stem = (
            one.cite_key if one is not None and one.cite_key else "publications"
        ) or "publications"
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:60]
        response = HttpResponse(body, content_type=f"{fmt.content_type}; charset=utf-8")
        response["Content-Disposition"] = content_disposition_header(
            True, f"{stem}.{fmt.extension}"
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response

    # ---------------------------------------------------------------------- the page

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        mine = list(Publication.objects.for_user(user).only("title", "date", "doi"))
        entries = references.held(self.request.session.get(references.SESSION_KEY))
        context["formats"] = list(references.FORMATS.values())
        context["one"] = self.chosen_publication(self.request)
        context["count"] = len(mine)
        context["entries"] = [
            {
                **entry,
                "index": index,
                "held": lookups.is_held(entry, mine),
                "type_label": publications.TYPES.get(
                    entry.get("entry_type"), publications.TYPES["misc"]
                ).label,
                "year": publications.year_of(entry.get("date") or ""),
            }
            for index, entry in enumerate(entries)
        ]
        context["notes"] = self.request.session.get(references.SESSION_KEY + "-notes") or {}
        context["lookups_on"] = lookups_offered(user)
        context["most_dois"] = MAX_DOIS
        context["most_entries"] = references.MAX_ENTRIES
        context["max_mb"] = references.MAX_BYTES // (1024 * 1024)
        context["back"] = "resume:overview"
        return context

    # --------------------------------------------------------------------- importing

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        action = request.POST.get("action")
        if action == "forget":
            self.forget(request)
            return redirect("resume:references")
        if action == "confirm":
            return self.confirm(request)
        if action == "dois":
            return self.from_dois(request)
        return self.read_file(request)

    @staticmethod
    def forget(request: HttpRequest) -> None:
        request.session.pop(references.SESSION_KEY, None)
        request.session.pop(references.SESSION_KEY + "-notes", None)

    def read_file(self, request: HttpRequest) -> HttpResponse:
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a file first."))
            return redirect("resume:references")
        try:
            reading = references.read(upload.read(references.MAX_BYTES + 1), upload.name or "")
        except references.ReferencesRefused as refused:
            messages.error(request, str(refused))
            return redirect("resume:references")
        if not reading.entries:
            messages.warning(
                request, _("That file was read, and there was no publication in it to add.")
            )
            return redirect("resume:references")
        request.session[references.SESSION_KEY] = reading.entries
        request.session[references.SESSION_KEY + "-notes"] = {
            "format": references.FORMATS[reading.format].label,
            "skipped": reading.skipped,
            "beyond": reading.beyond,
            "left_out": reading.left_out,
        }
        return redirect("resume:references")

    def from_dois(self, request: HttpRequest) -> HttpResponse:
        """DOIs typed, each looked up once, the answers added to the list to review."""
        if not lookups_offered(request.user):
            raise Http404
        typed = []
        for word in re.split(r"[\s,;]+", request.POST.get("dois", "")[:4000]):
            doi = lookups.doi_from(publications.normalise_doi(word))
            if doi and doi.lower() not in {one.lower() for one in typed}:
                typed.append(doi)
        if not typed:
            messages.error(request, _("Type at least one DOI first."))
            return redirect("resume:references")
        if len(typed) > MAX_DOIS:
            messages.error(
                request,
                ngettext(
                    "Type at most %(count)d DOI at a time; nothing was asked.",
                    "Type at most %(count)d DOIs at a time; nothing was asked.",
                    MAX_DOIS,
                )
                % {"count": MAX_DOIS},
            )
            return redirect("resume:references")
        held = references.held(request.session.get(references.SESSION_KEY))
        found = 0
        for doi in typed:
            try:
                throttle.lookup(request.user)
                record = lookups.from_doi(doi)
            except throttle.TooOften as too_often:
                messages.error(request, str(too_often))
                break
            except lookups.LookupRefused as refused:
                messages.error(request, _("%(doi)s: %(reason)s") % {"doi": doi, "reason": refused})
                continue
            if len(held) < references.MAX_ENTRIES and record.values.get("title"):
                held.append(
                    {
                        name: value
                        for name, value in record.values.items()
                        if name in references.FIELDS
                    }
                )
                found += 1
        if found:
            request.session[references.SESSION_KEY] = held
            notes = request.session.get(references.SESSION_KEY + "-notes") or {}
            notes.setdefault("format", _("DOI lookup"))
            request.session[references.SESSION_KEY + "-notes"] = notes
            messages.success(
                request,
                ngettext(
                    "%(count)d publication found. Nothing is added until you tick it.",
                    "%(count)d publications found. Nothing is added until you tick them.",
                    found,
                )
                % {"count": found},
            )
        return redirect("resume:references")

    def confirm(self, request: HttpRequest) -> HttpResponse:
        entries = references.held(request.session.get(references.SESSION_KEY))
        ticked = sorted(
            {
                int(value)
                for value in request.POST.getlist("entry")
                if value.isdecimal() and len(value) < 6 and int(value) < len(entries)
            }
        )
        if not ticked:
            messages.info(request, _("Nothing was ticked, so nothing was added."))
            return redirect("resume:references")
        added = create_from(request.user, [entries[index] for index in ticked])
        self.forget(request)
        messages.success(
            request,
            ngettext(
                "Added %(count)d publication to Your career. Nothing was overwritten.",
                "Added %(count)d publications to Your career. Nothing was overwritten.",
                added,
            )
            % {"count": added},
        )
        return redirect(reverse_overview())


def reverse_overview() -> str:
    from django.urls import reverse

    return reverse("resume:overview") + "#section-publication"


def create_from(owner, entries: list[dict]) -> int:
    """Each entry as a publication of ``owner``'s, held again to the rules, a key kept if free."""
    taken = set(
        Publication.objects.for_user(owner).exclude(cite_key="").values_list("cite_key", flat=True)
    )
    added = 0
    with transaction.atomic():
        for entry in entries:
            values = clean_entry(entry, taken)
            if not values.get("title"):
                continue
            item = Publication(owner=owner, **values)
            item.save()
            taken.add(item.cite_key)
            ordering.place_new(item)
            added += 1
    return added
