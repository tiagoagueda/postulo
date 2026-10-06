"""Editing the career record.

One set of views serves every kind of entry, dispatched through the registry. The
alternative — four view classes per model — would be twenty-four classes that differ
only in a noun.
"""

from __future__ import annotations

import datetime as dt
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from postulo.core import languages, throttle
from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin, OwnerFormMixin
from postulo.core.redirects import safe_next
from postulo.jobs import esco
from postulo.jobs.views import UserFormKwargsMixin
from postulo.plugins import base

from . import companies, importing, ordering, translating
from . import forms as resume_forms
from .models import (
    Certification,
    Course,
    DrivingLicence,
    Education,
    Experience,
    Honour,
    LanguageSkill,
    Link,
    Membership,
    Proficiency,
    Project,
    Publication,
    Skill,
    SkillGroup,
)
from .registry import OVERVIEW_ORDER, SECTIONS

logger = logging.getLogger(__name__)


#: The entries whose company is the body that gave them: a notice follows one with no industry
#: from the NACE list, and never refuses the entry (#686, #693).
LINKED_BY_GIVER = (Certification, Honour, Membership)


def get_section(slug: str):
    """Look up a section, or 404. Guards the URL against arbitrary model names."""
    try:
        return SECTIONS[slug]
    except KeyError as exc:
        raise Http404(f"Unknown section {slug!r}") from exc


def _for_overview(model, user):
    """One section's rows, a skill group with its skills so it does not ask twice (#559)."""
    rows = model.objects.for_user(user)
    return rows.prefetch_related("skills") if model is SkillGroup else rows


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
                "items": list(_for_overview(SECTIONS[slug].model, user)),
            }
            for slug in OVERVIEW_ORDER
        ]
        # The sidebar: one anchor per section, with how many entries it holds. A section
        # with nothing in it is listed all the same -- that is how somebody finds out the
        # page can hold their languages at all (#175).
        context["section_nav"] = [
            {
                "href": f"#section-{section['spec'].slug}",
                "anchor": f"section-{section['spec'].slug}",
                "label": section["spec"].plural,
                "icon": "",
                "count": len(section["items"]),
                "current": "",
            }
            for section in context["sections"]
        ]
        context["skills"] = Skill.objects.for_user(user).select_related("group")
        # Skills with no group: saved before a group was required, or read from a file that
        # names none. They are drawn in a block of their own so that none is out of sight (#617).
        context["ungrouped_skills"] = Skill.objects.for_user(user).filter(group__isnull=True)
        # One query for the whole page rather than one per entry: a generic link has no
        # join to follow, so the batch lookup is what answers for it (#131).
        everything = [item for section in context["sections"] for item in section["items"]]
        spoken = translating.languages_by_entry(everything)
        # Keyed by the entry itself rather than by its content type and id: a template can
        # say `map|get_item:item` and cannot say `map|get_item:(ct, pk)`.
        context["languages_by_entry"] = {
            entry: [languages.native_name(code, code) for code in codes]
            for entry in everything
            if (codes := spoken.get(translating.key_of(entry)))
        }
        context["counts"] = {
            "experience": Experience.objects.for_user(user).count(),
            "education": Education.objects.for_user(user).count(),
            "project": Project.objects.for_user(user).count(),
            "publication": Publication.objects.for_user(user).count(),
            "skill": Skill.objects.for_user(user).count(),
            "certification": Certification.objects.for_user(user).count(),
            "honour": Honour.objects.for_user(user).count(),
            "membership": Membership.objects.for_user(user).count(),
            "language": LanguageSkill.objects.for_user(user).count(),
            "driving_licence": DrivingLicence.objects.for_user(user).count(),
            "course": Course.objects.for_user(user).count(),
            "skill_group": SkillGroup.objects.for_user(user).count(),
        }
        return context


class SkillSuggestionsView(LoginRequiredMixin, View):
    """The ESCO skills whose names begin with what has been typed in the skill box (#266).

    A fragment of `<option>` elements for the box's `<datalist>`, which htmx fills as
    somebody types: fourteen thousand names are too many to send with the page, and a
    prefix answered with a few of them is not. In the reader's language, then in the one
    their career record is written in; the English names where the classification publishes
    neither. With scripts off nothing asks, and the box is the plain text box it always was.

    **Nothing here is anybody's.** The names are the classification's, and the one thing
    read of the person is which languages to answer in. It still needs an account, because
    everything on this instance does, and it is bounded per account by
    `POSTULO_SUGGESTION_RATE`: a box that asks at every pause in typing is a way of making
    the server search a list as fast as a script can ask. What was typed is cut to the
    length a name may have and never goes anywhere but the search.
    """

    template_name = "resume/partials/skill_suggestions.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        try:
            throttle.consume(
                "skill-suggestions", request.user, throttle.rate_for("POSTULO_SUGGESTION_RATE")
            )
        except throttle.TooOften as too_often:
            refusal = HttpResponse(str(too_often), status=429, content_type="text/plain")
            refusal["Retry-After"] = str(too_often.retry_after)
            return refusal
        limit = Skill._meta.get_field("name").max_length
        typed = (request.GET.get("name") or "")[:limit]
        names = esco.skill_suggestions(
            typed,
            languages.current(),
            translating.record_language_of(request.user),
        )
        response = render(request, self.template_name, {"names": names})
        # What one person typed, answered for them: no cache between them and it.
        response["Cache-Control"] = "private, no-store"
        return response


class ResumeItemTranslationsView(OwnedObjectMixin, View):
    """What one entry says in the other languages somebody writes CVs in.

    One screen per entry rather than a column per language on the editing form: most people
    translate one or two entries into one language, and a form that asked for every field in
    every language would be a wall nobody fills in. The list of what has been translated is
    on the same page, because "which of these did I do?" is the question that brings somebody
    back here (#131).
    """

    template_name = "resume/translations.html"

    def setup(self, request: HttpRequest, *args, **kwargs) -> None:
        super().setup(request, *args, **kwargs)
        self.section = get_section(kwargs["section"])

    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

    def chosen_language(self, request) -> str:
        """The language being translated into, as Postulo writes one, or nothing.

        From the address or the form, and so from anybody: what is not shaped like a
        language tag is no language, the page is drawn without a form, and nothing is
        saved under it. It used to be lower-cased and believed (#620).
        """
        asked = request.GET.get("language") or request.POST.get("language")
        return languages.tag(asked) if languages.well_formed(asked) else ""

    def context(self, entry, language: str, form=None) -> dict:
        stored = translating.stored_for(entry)
        return {
            "section": self.section,
            "entry": entry,
            "language": language,
            "language_name": languages.native_name(language, language),
            "record_language": languages.native_name(translating.record_language_of(entry.owner)),
            "translatable": translating.fields_for(entry),
            "held": [
                (
                    code,
                    languages.native_name(code, code),
                    [translating.field_label(entry, name) for name in sorted(fields)],
                )
                for code, fields in sorted(stored.items())
            ],
            "form": form,
            "add_form": resume_forms.AddLanguageForm(exclude=stored),
            # What a CV in this language prints for a skill where the box is left empty,
            # where that is the classification's name rather than the name as written: the
            # fallback is only ever one when the page says so (#266).
            "classified": (
                translating.classified_names([entry], language, entry.owner).get(entry.pk, "")
                if language
                else ""
            ),
            # Recognised by the classification this instance holds, which is the one that
            # decides what prints; an identifier the file no longer has decides nothing.
            "recognised": bool(getattr(entry, "esco_name", "")),
        }

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        entry = self.get_object()
        language = self.chosen_language(request)
        form = None
        if language and translating.fields_for(entry):
            form = resume_forms.TranslationForm(
                entry=entry,
                language=language,
                initial=translating.stored_for(entry).get(language, {}),
            )
        return render(request, self.template_name, self.context(entry, language, form))

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        entry = self.get_object()
        language = self.chosen_language(request)
        if not language or not translating.fields_for(entry):
            return redirect(reverse("resume:item_languages", args=[self.section.slug, entry.pk]))
        form = resume_forms.TranslationForm(request.POST, entry=entry, language=language)
        if not form.is_valid():  # pragma: no cover - every field is optional free text
            return render(request, self.template_name, self.context(entry, language, form))
        kept = form.save()
        messages.success(
            request,
            _("Saved in %(language)s.") % {"language": languages.native_name(language, language)}
            if kept
            else _("Cleared: this entry prints its original text in %(language)s.")
            % {"language": languages.native_name(language, language)},
        )
        return redirect(
            f"{reverse('resume:item_languages', args=[self.section.slug, entry.pk])}"
            f"?language={language}"
        )


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
        if self.section.slug == "reference":
            context.update(self.contact_links())
        return context

    def contact_links(self) -> dict:
        """Where a reference's person is added and edited: on the contact's own pages, which
        return here once saved (`next`, read through `safe_next`) (#696)."""
        here = urlencode({"next": self.request.get_full_path()})
        links = {"contact_add_url": f"{reverse('jobs:contact_create')}?{here}"}
        contact_id = getattr(getattr(self, "object", None), "contact_id", None)
        if contact_id:
            links["contact_edit_url"] = (
                f"{reverse('jobs:contact_update', args=[contact_id])}?{here}"
            )
        return links


def say_what_is_missing(request: HttpRequest, form) -> None:
    """For a publication, what BibTeX asks of its type and the entry lacks: a hint, after the
    save and never instead of it (#687)."""
    lacking = form.missing_labels() if hasattr(form, "missing_labels") else []
    if lacking:
        messages.info(
            request,
            _(
                "A bibliography usually also gives: %(fields)s. Nothing is wrong without "
                "them; add them if you have them."
            )
            % {"fields": ", ".join(lacking)},
        )


def say_school_added(request, form) -> None:
    """Say that an institution not among the person's companies was added, and as what (#685)."""
    company = getattr(form, "added_school", None)
    if company is not None:
        messages.info(
            request,
            _("“%(name)s” was added to your companies as Education.") % {"name": company.name},
        )


def say_what_is_unclassified(request: HttpRequest, form) -> None:
    """For a certification whose issuer has no industry from the NACE list, one line.

    A notice after the save and never a reason to refuse it (#686): a company is added with
    no industry on purpose, and it is classified on its own page.
    """
    company = getattr(form.instance, "company", None)
    if isinstance(form.instance, LINKED_BY_GIVER) and company and not company.has_nace_industry:
        messages.info(
            request,
            _("%(name)s has none of its industries from the NACE list yet.")
            % {"name": company.name},
        )


class ResumeItemCreateView(OwnedObjectMixin, SectionFormMixin, OwnerFormMixin, CreateView):
    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)

    def get_initial(self) -> dict:
        initial = super().get_initial()
        # *Add a skill* under a group opens the form with that group chosen (#617); a number
        # that is not one of the person's groups is not among the choices and so selects none.
        if self.section.slug == "skill" and self.request.GET.get("group", "").isdecimal():
            initial["group"] = self.request.GET["group"]
        return initial

    def form_valid(self, form):
        messages.success(self.request, _("Added."))
        say_what_is_missing(self.request, form)
        response = super().form_valid(form)
        say_school_added(self.request, form)
        say_what_is_unclassified(self.request, form)
        # Placed by its date, or last, unless the person typed a number themselves (#203).
        if "order" not in form.fields:
            ordering.place_new(self.object)
        return response


class ResumeItemUpdateView(OwnedObjectMixin, SectionFormMixin, UpdateView):
    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["translatable"] = bool(translating.fields_for(self.object))
        context["translated_into"] = [
            languages.native_name(code, code) for code in translating.languages_of(self.object)
        ]
        # Which ESCO skill the name was recognised as, said where the name is edited (#266).
        context["esco_name"] = getattr(self.object, "esco_name", "")
        # An institution linked to a company with no industry in Education says so (#685).
        company = getattr(self.object, "company", None)
        context["not_a_school"] = (
            self.section.slug == "education"
            and company is not None
            and not companies.is_school(company)
        )
        # A certification's issuer that has no NACE industry yet is said so, with a link (#686).
        company = getattr(self.object, "company", None)
        if isinstance(self.object, LINKED_BY_GIVER) and company and not company.has_nace_industry:
            context["unclassified_company"] = company
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Saved."))
        say_what_is_missing(self.request, form)
        response = super().form_valid(form)
        say_school_added(self.request, form)
        say_what_is_unclassified(self.request, form)
        return response


class ResumeItemDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("resume:overview")

    def setup(self, request: HttpRequest, *args, **kwargs) -> None:
        super().setup(request, *args, **kwargs)
        self.section = get_section(kwargs["section"])

    def get_queryset(self):
        return self.section.model.objects.for_user(self.request.user)


class ResumeItemMoveView(OwnedObjectMixin, View):
    """Move an entry past its neighbour, up or down.

    Two buttons rather than drag-and-drop: dragging does not fire on a touch screen and is
    not reachable from a keyboard, and two buttons work everywhere. A swap with the
    neighbour and a renumbering of the section, not a nudge of the number: the number
    nudged by one was invisible from the page more often than not (#203).
    """

    def get_queryset(self):
        return get_section(self.kwargs["section"]).model.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, section: str, pk: int, direction: str) -> HttpResponse:
        item = get_object_or_404(self.get_queryset(), pk=pk)
        ordering.move(item, direction)
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
                "publications": Publication.objects.for_user(user),
                "links": Link.objects.for_user(user),
                "skill_groups": SkillGroup.objects.for_user(user).prefetch_related("skills"),
                "certifications": Certification.objects.for_user(user),
                "honours": Honour.objects.for_user(user),
                "memberships": Membership.objects.for_user(user),
                "languages": LanguageSkill.objects.for_user(user),
                "driving_licences": DrivingLicence.objects.for_user(user),
                "courses": Course.objects.for_user(user),
            },
        )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class LinkCheckView(OwnedObjectMixin, View):
    """*Check*: ask once whether a link still answers, because a person asked.

    One link with a primary key, or all of them without. Postulo checks nothing on a
    schedule and nothing on its own. Each link is saved by its own `save()`, so the view
    holds no transaction while it waits on somebody else's server (#357).
    """

    def get_queryset(self):
        return Link.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int | None = None) -> HttpResponse:
        from . import links as link_checks

        fallback = reverse("resume:overview")
        if pk is not None:
            link = get_object_or_404(self.get_queryset(), pk=pk)
            try:
                link_checks.check(link)
            except throttle.TooOften as too_often:
                messages.error(request, str(too_often))
                return redirect(safe_next(request, fallback))
            if link.is_broken:
                messages.error(
                    request,
                    _("%(title)s did not answer: %(detail)s")
                    % {"title": link.title, "detail": link.check_detail},
                )
            else:
                messages.success(request, _("%(title)s still answers.") % {"title": link.title})
            return redirect(safe_next(request, fallback))

        ok, broken, left = link_checks.check_all(request.user)
        if left and throttle.spent("fetch", request.user, throttle.rate_for("POSTULO_FETCH_RATE")):
            messages.warning(
                request,
                ngettext(
                    "You have fetched as many addresses as are allowed for now: "
                    "%(count)d link was not checked. Check again later for the rest.",
                    "You have fetched as many addresses as are allowed for now: "
                    "%(count)d links were not checked. Check again later for the rest.",
                    left,
                )
                % {"count": left},
            )
        elif left:
            messages.warning(
                request,
                ngettext(
                    "Time ran out: %(count)d link was not reached. Check again for the rest.",
                    "Time ran out: %(count)d links were not reached. Check again for the rest.",
                    left,
                )
                % {"count": left},
            )
        elif not ok and not broken:
            messages.info(request, _("There are no links to check."))
        elif broken:
            messages.warning(
                request,
                _("%(ok)s, %(broken)s; the ones that did not say why.")
                % {
                    "ok": ngettext("%(count)d link answered", "%(count)d links answered", ok)
                    % {"count": ok},
                    "broken": ngettext("%(count)d did not", "%(count)d did not", broken)
                    % {"count": broken},
                },
            )
        else:
            messages.success(
                request,
                ngettext(
                    "All %(count)d link still answers.",
                    "All %(count)d links still answer.",
                    ok,
                )
                % {"count": ok},
            )
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
        if upload.size > base.MAX_IMPORT_BYTES:
            messages.error(
                request,
                _("That file is over %(limit)s MB. A CV is not that big.")
                % {"limit": base.MAX_IMPORT_BYTES // (1024 * 1024)},
            )
            return redirect("resume:europass_import")

        # Refuse first, then ask what is installed rather than naming Europass. The
        # refusals belong to the kind and apply whoever would have read the file. One
        # importer ships in the box and is the only one that answers today; the point is
        # that the second one need not be a change to this view.
        data = upload.read()
        from postulo.plugins.policy import plugins_for

        try:
            base.refuse_unreadable(data)
            importer = None
            for plugin in plugins_for(request.user, "importer"):
                # A plugin that raises is logged and skipped, as capture does (#611): one
                # faulty add-on must not stop the importers behind it.
                try:
                    claimed = plugin.can_handle(data, upload.name or "")
                except Exception:
                    logger.exception("Importer %r could not be asked about a file", plugin.name)
                    continue
                if claimed:
                    importer = plugin
                    break
            if importer is None:
                # Named from the registry, for the same reason the lookup above is: with a
                # second importer installed, the sentence has to say so (#105).
                raise base.ImportRefused(
                    _("Nothing installed here reads that file. What is installed reads: %(what)s.")
                    % {
                        "what": ", ".join(
                            str(p.label) for p in plugins_for(request.user, "importer")
                        )
                    }
                )
            try:
                record = importer.read(data)
            except base.ImportRefused:
                raise
            except Exception:
                logger.exception("Importer %r failed while reading a file", importer.name)
                raise base.ImportRefused(
                    _("The importer %(name)s failed while reading that file. Nothing was imported.")
                    % {"name": importer.label}
                ) from None
        except base.ImportRefused as error:
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
            report = importing.apply(request.user, record)

        request.session.pop(self.SESSION_KEY, None)
        request.session.pop(f"{self.SESSION_KEY}_data", None)
        messages.success(
            request,
            ngettext(
                "Added %(count)s entry. Nothing was overwritten; anything duplicated is "
                "yours to delete.",
                "Added %(count)s entries. Nothing was overwritten; anything duplicated is "
                "yours to delete.",
                report.total,
            )
            % {"count": report.total},
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
    "memberships": _("Memberships"),
    "references": _("References"),
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

#: Which Europass format was read, in words. The PDF is named because it is the file a
#: person chose: saying only "XML" about it would describe a file they never saw (#244).
SOURCE_LABELS = {
    "candidate": _("Read as Europass XML, the format europass.europa.eu has written since 2020."),
    "pdf-candidate": _(
        "Read from the Europass XML attached to the PDF, the format europass.europa.eu has "
        "written since 2020."
    ),
    "xml": _(
        "Read as Europass XML in the older format, the one the Europass editor wrote until 2020."
    ),
    "pdf-xml": _(
        "Read from the Europass XML attached to the PDF, in the older format the Europass "
        "editor wrote until 2020."
    ),
    "json": _(
        "Read as Europass JSON, the older format as the Europass web service wrote it until 2020."
    ),
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
            # Said out loud on the review page rather than quietly filled in with B1. A file
            # that states no CEFR level is the ordinary case, and the honest answer to "how
            # good is your German" is the one the person gives afterwards (#235).
            "unset": not row.get("proficiency"),
            "levels": [
                {"part": LEVEL_LABELS.get(part, part), "level": level}
                for part, level in (row.get("levels") or {}).items()
            ],
        }
        for row in held.get("languages", [])
    ]
    shown["locale"] = languages.native_name(held.get("locale", ""), held.get("locale", ""))
    return shown


def _proficiency_label(value):
    try:
        return Proficiency(value).label
    except ValueError:
        return value


def _summarise(record: importing.Record) -> dict:
    """What the review page shows: enough to recognise the file, not the whole of it."""
    return {
        "counts": record.counts(),
        "source": record.source,
        "locale": record.locale,
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
        "memberships": [{"organisation": row["organisation"]} for row in record.memberships],
        # Each person, by name, before anything is written: they become contacts (#696).
        "references": [
            {"name": row["name"], "relationship": row.get("relationship", "")}
            for row in record.references
        ],
    }


def _to_session(record: importing.Record) -> dict:
    """The record as something a session can hold: dates become strings."""
    return {
        "person": record.person,
        "locale": record.locale,
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
        "memberships": [
            {
                **row,
                "start_date": _iso(row.get("start_date")),
                "end_date": _iso(row.get("end_date")),
            }
            for row in record.memberships
        ],
        "references": record.references,
    }


def _from_session(raw: dict) -> importing.Record:
    return importing.Record(
        person=raw.get("person", {}),
        locale=raw.get("locale", ""),
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
        memberships=[
            {
                **row,
                "start_date": _date(row.get("start_date")),
                "end_date": _date(row.get("end_date")),
            }
            for row in raw.get("memberships", [])
        ],
        references=raw.get("references", []),
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
