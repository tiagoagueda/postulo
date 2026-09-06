"""Editing the career record.

One set of views serves every kind of entry, dispatched through the registry. The
alternative — four view classes per model — would be twenty-four classes that differ
only in a noun.
"""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin

from . import europass
from .models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Link,
    Proficiency,
    Project,
    Skill,
    SkillGroup,
)
from .registry import OVERVIEW_ORDER, SECTIONS


def get_section(slug: str):
    """Look up a section, or 404. Guards the URL against arbitrary model names."""
    try:
        return SECTIONS[slug]
    except KeyError as exc:
        raise Http404(f"Unknown section {slug!r}") from exc


class ResumeOverviewView(OwnedObjectMixin, TemplateView):
    """Everything you have written about yourself, on one page."""

    template_name = "resume/overview.html"

    def get_queryset(self):
        # Needed only to satisfy OwnedObjectMixin's login requirement.
        return Experience.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["sections"] = [
            {
                "spec": SECTIONS[slug],
                "items": SECTIONS[slug].model.objects.for_user(user),
            }
            for slug in OVERVIEW_ORDER
        ]
        context["skills"] = Skill.objects.for_user(user).select_related("group")
        context["counts"] = {
            "experience": Experience.objects.for_user(user).count(),
            "education": Education.objects.for_user(user).count(),
            "project": Project.objects.for_user(user).count(),
            "skill": Skill.objects.for_user(user).count(),
            "certification": Certification.objects.for_user(user).count(),
            "language": LanguageSkill.objects.for_user(user).count(),
            "skill_group": SkillGroup.objects.for_user(user).count(),
        }
        return context


class SectionFormMixin(UserFormKwargsMixin):
    """Shared plumbing for the create and edit screens."""

    template_name = "resume/item_form.html"
    success_url = reverse_lazy("resume:overview")

    def setup(self, request: HttpRequest, *args, **kwargs) -> None:
        super().setup(request, *args, **kwargs)
        self.section = get_section(kwargs["section"])

    @property
    def model(self):  # type: ignore[override]
        return self.section.model

    def get_form_class(self):
        return self.section.form

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["section"] = self.section
        return context


class ResumeItemCreateView(OwnedObjectMixin, SectionFormMixin, OwnerFormMixin, CreateView):
    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Added."))
        return super().form_valid(form)


class ResumeItemUpdateView(OwnedObjectMixin, SectionFormMixin, UpdateView):
    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Saved."))
        return super().form_valid(form)


class ResumeItemDeleteView(OwnedObjectMixin, DeleteView):
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("resume:overview")

    def setup(self, request: HttpRequest, *args, **kwargs) -> None:
        super().setup(request, *args, **kwargs)
        self.section = get_section(kwargs["section"])

    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)


class ResumeItemMoveView(OwnedObjectMixin, View):
    """Nudge an entry up or down.

    Ordering is a small integer rather than drag-and-drop: dragging needs JavaScript that
    the Content-Security-Policy would have to be loosened for, and two buttons work
    without any.
    """

    def get_queryset(self):
        return get_section(self.kwargs["section"]).model.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, section: str, pk: int, direction: str) -> HttpResponse:
        item = get_object_or_404(self.get_queryset(), pk=pk)
        item.order = max(0, item.order + (-1 if direction == "up" else 1))
        item.save(update_fields=["order", "updated_at"])
        return redirect(safe_next(request, reverse("resume:overview")))


class ResumePreviewView(OwnedObjectMixin, View):
    """Show the master record as it would read, before any CV selects from it."""

    def get_queryset(self):
        return Experience.objects.for_user(self.request.user)

    def get(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        return render(
            request,
            "resume/preview.html",
            {
                "experience": Experience.objects.for_user(user),
                "education": Education.objects.for_user(user),
                "projects": Project.objects.for_user(user),
                "skill_groups": SkillGroup.objects.for_user(user).prefetch_related("skills"),
                "certifications": Certification.objects.for_user(user),
                "languages": LanguageSkill.objects.for_user(user),
            },
        )


class LinkCheckView(OwnedObjectMixin, View):
    """*Check*: ask once whether a link still answers, because a person asked.

    One link with a primary key, or all of them without. Postulo checks nothing on a
    schedule and nothing on its own.
    """

    def get_queryset(self):
        return Link.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int | None = None) -> HttpResponse:
        from . import links as link_checks

        fallback = reverse("resume:overview")
        if pk is not None:
            link = get_object_or_404(self.get_queryset(), pk=pk)
            link_checks.check(link)
            if link.is_broken:
                messages.error(
                    request,
                    _("%(title)s did not answer: %(detail)s")
                    % {"title": link.title, "detail": link.check_detail},
                )
            else:
                messages.success(request, _("%(title)s still answers.") % {"title": link.title})
            return redirect(safe_next(request, fallback))

        ok, broken = link_checks.check_all(request.user)
        if not ok and not broken:
            messages.info(request, _("There are no links to check."))
        elif broken:
            messages.warning(
                request,
                _("%(ok)d answered, %(broken)d did not; the ones that did not say why.")
                % {"ok": ok, "broken": broken},
            )
        else:
            messages.success(request, _("All %(ok)d links still answer.") % {"ok": ok})
        return redirect(safe_next(request, fallback))


class EuropassImportView(LoginRequiredMixin, TemplateView):
    """Read a Europass file, show what is in it, and write it only when told to.

    Two steps on one address, the same shape as the spreadsheet import: the file is read
    and held in the session, the page says what was found, and nothing reaches the career
    record until somebody has seen the list and pressed the button. Capture and suggestions
    both work this way, and for the same reason -- **nothing is saved on a guess**.

    What is held between the two steps is the parsed record, not the file. There is no
    reason to keep somebody's CV on the server for longer than it takes to read it.
    """

    template_name = "resume/europass_import.html"
    SESSION_KEY = "europass_import"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        held = self.request.session.get(self.SESSION_KEY)
        context["section_title"] = _("Import a Europass CV")
        context["found"] = _for_display(held) if held else None
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "forget":
            request.session.pop(self.SESSION_KEY, None)
            return redirect("resume:europass_import")

        if request.POST.get("action") == "confirm":
            return self._write(request)

        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a Europass file first."))
            return redirect("resume:europass_import")
        if upload.size > europass.MAX_BYTES:
            messages.error(
                request,
                _("That file is over %(limit)s MB. A CV is not that big.")
                % {"limit": europass.MAX_BYTES // (1024 * 1024)},
            )
            return redirect("resume:europass_import")

        try:
            record = europass.read(upload.read())
        except europass.EuropassError as error:
            messages.error(request, str(error))
            return redirect("resume:europass_import")

        if record.is_empty:
            messages.warning(
                request, _("That file was read, and there was nothing in it to import.")
            )
            return redirect("resume:europass_import")

        request.session[self.SESSION_KEY] = _summarise(record)
        request.session[f"{self.SESSION_KEY}_data"] = _to_session(record)
        return redirect("resume:europass_import")

    def _write(self, request):
        raw = request.session.get(f"{self.SESSION_KEY}_data")
        if not raw:
            messages.error(request, _("There is nothing waiting to be imported."))
            return redirect("resume:europass_import")

        record = _from_session(raw)
        with transaction.atomic():
            report = europass.apply(request.user, record)

        request.session.pop(self.SESSION_KEY, None)
        request.session.pop(f"{self.SESSION_KEY}_data", None)
        messages.success(
            request,
            _(
                "Added %(total)s entries. Nothing was overwritten; anything duplicated is "
                "yours to delete."
            )
            % {"total": report.total},
        )
        for note in report.skipped:
            messages.warning(request, note)
        return redirect("resume:overview")


#: Europass's own words on the left, Postulo's on the right. The session holds the keys,
#: so a language changed between reading the file and confirming it still reads properly.
COUNT_LABELS = {
    "experience": _("Positions"),
    "education": _("Qualifications"),
    "languages": _("Languages"),
    "skills": _("Skills"),
    "projects": _("Projects and achievements"),
}

PERSON_LABELS = {
    "first_name": _("first name"),
    "last_name": _("surname"),
    "email": _("email address"),
    "phone": _("telephone"),
    "website": _("website"),
    "location": _("where you live"),
    "headline": _("headline"),
    "orcid": _("ORCID"),
}

SOURCE_LABELS = {
    "xml": _("Read as Europass XML, the format the CV editor produced."),
    "json": _("Read as Europass JSON, the format europass.europa.eu exports."),
}

LEVEL_LABELS = {
    "Listening": _("listening"),
    "Reading": _("reading"),
    "SpokenInteraction": _("conversation"),
    "SpokenProduction": _("speaking"),
    "Writing": _("writing"),
}


def _for_display(held: dict) -> dict:
    """The held summary with its keys turned into words somebody can read."""
    shown = dict(held)
    shown["counts"] = [
        {"label": COUNT_LABELS.get(key, key), "total": total}
        for key, total in held.get("counts", {}).items()
        if total
    ]
    shown["source"] = SOURCE_LABELS.get(held.get("source"), "")
    shown["person"] = [PERSON_LABELS.get(key, key) for key in held.get("person", {})]
    shown["languages"] = [
        {
            "name": row["name"],
            "proficiency": _proficiency_label(row.get("proficiency")),
            "levels": [
                {"part": LEVEL_LABELS.get(part, part), "level": level}
                for part, level in (row.get("levels") or {}).items()
            ],
        }
        for row in held.get("languages", [])
    ]
    return shown


def _proficiency_label(value):
    try:
        return Proficiency(value).label
    except ValueError:
        return value


def _summarise(record: europass.Record) -> dict:
    """What the review page shows: enough to recognise the file, not the whole of it."""
    return {
        "counts": record.counts(),
        "source": record.source,
        "skipped": record.skipped,
        "person": record.person,
        "experience": [
            {"role": row["role"], "organisation": row["organisation"]} for row in record.experience
        ],
        "education": [
            {"qualification": row["qualification"], "institution": row["institution"]}
            for row in record.education
        ],
        "languages": [
            {"name": row["name"], "proficiency": row["proficiency"], "levels": row["levels"]}
            for row in record.languages
        ],
        "skill_groups": record.skill_groups,
        "projects": [{"name": row["name"]} for row in record.projects],
    }


def _to_session(record: europass.Record) -> dict:
    """The record as something a session can hold: dates become strings."""
    return {
        "person": record.person,
        "experience": [
            {
                **row,
                "start_date": _iso(row["start_date"]),
                "end_date": _iso(row["end_date"]),
            }
            for row in record.experience
        ],
        "education": [
            {
                **row,
                "start_date": _iso(row["start_date"]),
                "end_date": _iso(row["end_date"]),
            }
            for row in record.education
        ],
        "languages": record.languages,
        "skill_groups": record.skill_groups,
        "projects": record.projects,
    }


def _from_session(raw: dict) -> europass.Record:
    return europass.Record(
        person=raw.get("person", {}),
        experience=[
            {
                **row,
                "start_date": _date(row.get("start_date")),
                "end_date": _date(row.get("end_date")),
            }
            for row in raw.get("experience", [])
        ],
        education=[
            {
                **row,
                "start_date": _date(row.get("start_date")),
                "end_date": _date(row.get("end_date")),
            }
            for row in raw.get("education", [])
        ],
        languages=raw.get("languages", []),
        skill_groups=raw.get("skill_groups", []),
        projects=raw.get("projects", []),
    )


def _iso(value):
    return value.isoformat() if value else None


def _date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
