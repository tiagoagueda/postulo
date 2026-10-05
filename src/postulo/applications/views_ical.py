"""The calendar as iCalendar: out, and in (#661).

Three addresses and none takes an id. What is downloaded is the calendar of whoever is signed
in, and what is uploaded is added to the account of whoever is signed in, after a review:
there is no id in any of them for somebody to change.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View

from . import agenda, ical_import, ical_reader
from .models import Reminder


def _calendar_response(text: str, filename: str) -> HttpResponse:
    response = HttpResponse(text, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    # Somebody's dates and the names of the companies they are talking to.
    response["Cache-Control"] = "private, max-age=0, no-store"
    return response


class CalendarDownloadView(LoginRequiredMixin, View):
    """What the calendar page is showing, as an .ics file.

    The same address parameters the page reads -- the period and the kinds its legend has
    switched on -- and the same list it draws, so what the file holds is what was on the
    screen. Interviews are meetings, deadlines are whole days and reminders are tasks.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        page = agenda.page_for(request.GET, request.user)
        events = [*page.overdue, *page.events, *page.further]
        text = agenda.download_for(events, request.build_absolute_uri)
        return _calendar_response(text, f"postulo-{page.start:%Y-%m-%d}.ics")


class ReminderCalendarView(LoginRequiredMixin, View):
    """Every reminder still to do, and those done in the last half year, as tasks.

    An address of its own: the interviews feed is what existing subscribers read, and what
    it means is not changed under them. A calendar application that keeps tasks lists
    these; one that does not ignores the file.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        reminders = agenda.feed_reminders(Reminder.objects.for_user(request.user))
        text = agenda.reminders_file(reminders, request.build_absolute_uri)
        return _calendar_response(text, "reminders.ics")


class CalendarImportView(LoginRequiredMixin, View):
    """Offer the file, read one, show what each entry would become, and make what is chosen.

    Two steps on one address, as the candidate file and the spreadsheet import have: a file
    is read and what it holds is kept in the session, the page lists every event and task
    with a choice for each, and nothing reaches the search until somebody has made the
    choices and pressed the button. **Nothing is made on a guess.**
    """

    template_name = "applications/ical_import.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        held = ical_import.held(request)
        context: dict = {"review": None}
        if held:
            rows = ical_import.plan(request.user, held["entries"])
            context["review"] = self._review(held, rows)
        return render(request, self.template_name, context)

    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action")
        if action == "forget":
            ical_import.forget(request)
            return redirect("applications:ical_import")
        if action == "confirm":
            return self._confirm(request)
        return self._read(request)

    @staticmethod
    def _review(held: dict, rows: list) -> dict:
        return {
            "filename": held["filename"],
            "notes": held["notes"],
            "rows": rows,
            "adds": ical_import.adds(rows),
        }

    def _read(self, request: HttpRequest) -> HttpResponse:
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a file first."))
            return redirect("applications:ical_import")
        # Asked of the upload before a byte of it is read, and asked again of the bytes by
        # `ical_reader.read`, which is what anything else calling it relies on.
        if upload.size > ical_reader.MAX_BYTES:
            messages.error(
                request,
                _("That file is larger than %(limit)s MB, so it was not read.")
                % {"limit": ical_reader.MAX_BYTES // (1024 * 1024)},
            )
            return redirect("applications:ical_import")
        try:
            reading = ical_reader.read(upload.read(ical_reader.MAX_BYTES + 1))
        except ical_reader.Refused as refused:
            messages.error(request, str(refused))
            return redirect("applications:ical_import")
        if not reading.entries:
            messages.warning(
                request, _("That file was read, and there were no events or tasks in it.")
            )
            return redirect("applications:ical_import")
        ical_import.hold(request, upload.name or "calendar.ics", reading.entries, reading.notes)
        return redirect("applications:ical_import")

    def _confirm(self, request: HttpRequest) -> HttpResponse:
        held = ical_import.held(request)
        if not held:
            messages.error(request, _("There is nothing waiting to be imported."))
            return redirect("applications:ical_import")
        rows = ical_import.plan(request.user, held["entries"], data=request.POST)
        adding = [row for row in rows if row.outcome == ical_import.ADD]
        # Every form is checked, so one entry asking for a time zone is not found out about
        # after the others have been made.
        valid = [row.form.is_valid() for row in adding]
        if not all(valid):
            messages.error(request, _("Some of the choices need another look."))
            return render(
                request, self.template_name, {"review": self._review(held, rows)}, status=200
            )
        report = ical_import.apply(request.user, rows, held["filename"])
        ical_import.forget(request)
        if report.total:
            messages.success(
                request,
                ngettext(
                    "One entry was added. Nothing you already had was changed.",
                    "%(total)s entries were added. Nothing you already had was changed.",
                    report.total,
                )
                % {"total": report.total},
            )
        else:
            messages.info(request, _("Nothing was added: every entry was left out."))
        return redirect("applications:calendar")
