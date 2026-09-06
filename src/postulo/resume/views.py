"""Editing the career record.

One set of views serves every kind of entry, dispatched through the registry. The
alternative — four view classes per model — would be twenty-four classes that differ
only in a noun.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin

from .models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Link,
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
