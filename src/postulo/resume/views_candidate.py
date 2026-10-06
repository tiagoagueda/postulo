"""One person's own record as a file: taking it out, and putting one back (#181).

Two addresses and neither takes an argument. What is downloaded is the record of whoever
is signed in, and what is uploaded is added to the account of whoever is signed in: there
is no id in either address for somebody to change.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.http import content_disposition_header
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django.views import View
from django.views.generic import TemplateView

from postulo.core import export
from postulo.core.models import WebLink

from . import candidate
from . import models as resume

#: Where a file that has been read waits to be confirmed. What is held is what was read of
#: it, not the file: there is no reason to keep somebody's career on the server for longer
#: than it takes them to look at it.
SESSION_KEY = "candidate_file"

#: What the page counts before offering the file, in the order the file is read back. The
#: three that have no heading of their own anywhere take the model's name for them, which
#: the catalogues already have.
COUNTED = (
    ("experience", gettext_lazy("Experience")),
    ("education", gettext_lazy("Education")),
    ("projects", gettext_lazy("Projects")),
    ("publications", gettext_lazy("Publications")),
    ("links", gettext_lazy("Links")),
    ("skill_groups", capfirst(resume.SkillGroup._meta.verbose_name_plural)),
    ("skills", gettext_lazy("Skills")),
    ("certifications", gettext_lazy("Certifications")),
    ("honours", gettext_lazy("Honours and awards")),
    ("memberships", gettext_lazy("Memberships")),
    ("driving_licences", gettext_lazy("Driving licences")),
    ("courses", gettext_lazy("Courses")),
    ("languages", gettext_lazy("Languages")),
    ("translations", capfirst(resume.Translation._meta.verbose_name_plural)),
    ("phone_numbers", gettext_lazy("Telephone numbers")),
    ("messaging_handles", gettext_lazy("Messaging")),
    ("postal_addresses", gettext_lazy("Postal addresses")),
    ("web_links", capfirst(WebLink._meta.verbose_name_plural)),
    ("identifiers", gettext_lazy("Identifiers")),
)


def _counted(user) -> list[dict]:
    found = export.candidate_counts(user)
    return [{"label": label, "total": found.get(key, 0)} for key, label in COUNTED]


def _held(request: HttpRequest) -> dict | None:
    """What is waiting in this session, or nothing.

    Nothing as well for anything that is not what `candidate.read` leaves there: a session
    written by an older Postulo is not worth an error page on every visit to this one.
    """
    held = request.session.get(SESSION_KEY)
    if isinstance(held, dict) and isinstance(held.get("details"), dict):
        return held
    request.session.pop(SESSION_KEY, None)
    return None


class CandidateFileView(LoginRequiredMixin, TemplateView):
    """Offer the file, read one, show what adding it would do, and add it when told to.

    Two steps on one address, the same shape as the Europass import and the spreadsheet
    one: a file is read and held in the session, the page says what was found and what
    would happen to each part of it, and nothing reaches the record until somebody has
    seen that and pressed the button. **Nothing is saved on a guess.**

    What the page shows is worked out when it is drawn, against the account as it stands,
    and worked out again when the button is pressed. What was read is the only thing kept
    between the two.
    """

    template_name = "resume/candidate_file.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        held = _held(self.request)
        context["review"] = candidate.plan(self.request.user, held) if held else None
        if not held:
            context["counts"] = _counted(self.request.user)
            context["filename"] = export.candidate_filename(self.request.user)
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        action = request.POST.get("action")
        if action == "forget":
            request.session.pop(SESSION_KEY, None)
            return redirect("resume:candidate_file")
        if action == "confirm":
            return self._add(request)

        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a file first."))
            return redirect("resume:candidate_file")
        # Asked of the upload before a byte of it is read, and asked again of the bytes
        # by `candidate.read`, which is what anything else calling it relies on.
        if upload.size > candidate.MAX_BYTES:
            messages.error(
                request,
                _("That file is larger than %(limit)s MB, so it was not read.")
                % {"limit": candidate.MAX_BYTES // (1024 * 1024)},
            )
            return redirect("resume:candidate_file")
        try:
            held = candidate.read(upload.read())
        except candidate.Refused as refused:
            messages.error(request, str(refused))
            return redirect("resume:candidate_file")
        if candidate.is_empty(held):
            messages.warning(
                request, _("That file was read, and there was nothing in it to import.")
            )
            return redirect("resume:candidate_file")

        request.session[SESSION_KEY] = held
        return redirect("resume:candidate_file")

    def _add(self, request: HttpRequest) -> HttpResponse:
        held = _held(request)
        if not held:
            messages.error(request, _("There is nothing waiting to be imported."))
            return redirect("resume:candidate_file")

        report = candidate.apply(request.user, held)
        request.session.pop(SESSION_KEY, None)
        if report.total:
            messages.success(
                request,
                ngettext(
                    "One entry was added to your record. Nothing you already had was "
                    "changed or removed.",
                    "%(total)s entries were added to your record. Nothing you already had "
                    "was changed or removed.",
                    report.total,
                )
                % {"total": report.total},
            )
        else:
            messages.info(
                request,
                _(
                    "Nothing was added. What that file holds is already in your record, or "
                    "could not be added."
                ),
            )
        if report.held_back:
            # The page may have said a number would be added which then was not: the
            # allowance for asking about numbers ran out between the page and the button.
            messages.warning(
                request,
                _("Some telephone numbers were not added. %(why)s") % {"why": report.held_back},
            )
        return redirect("resume:overview")


class CandidateDownloadView(LoginRequiredMixin, View):
    """The record of whoever is asking, as one JSON document arriving as a download.

    A link rather than a button that posts, which is what the whole-account export is: that
    one reads every record and every file an account owns, and this one reads a career. It
    changes nothing, so asking for it twice is asking for it once.

    Never kept by anything between here and the person: it is their address, their
    telephone number and where they have worked.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        response = JsonResponse(
            export.build_candidate_document(request.user),
            json_dumps_params={"ensure_ascii": False, "indent": 2},
        )
        response["Content-Disposition"] = content_disposition_header(
            True, export.candidate_filename(request.user)
        )
        response["Cache-Control"] = "private, max-age=0, no-store"
        return response
