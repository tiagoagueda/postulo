"""Printing the calendar: the page that says what the print will hold, and the PDF (#342).

`calendar_print` says what a printed calendar is and why; this is the two addresses around
it, shaped as a list's export is (#341).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from . import agenda, calendar_print

#: What the calendar's address may carry, and so what the print's carries: anything else in
#: the query is nobody's business here.
QUESTION = ("view", "on", "until", "month", "kinds", "q")


def _question(request: HttpRequest) -> str:
    """The calendar's own question from this request's address, and nothing else."""
    kept = request.GET.copy()
    for name in list(kept):
        if name not in QUESTION:
            kept.pop(name)
    return kept.urlencode()


class CalendarPrintView(LoginRequiredMixin, View):
    """What a print of the calendar will hold, and the choices for the document.

    Reached from *Print* beside the views on the Calendar page, which carries the page's
    address: the view, the day, the kinds shown and the search. It says which period and
    how many entries, and offers a theme, a language and a paper.
    """

    template_name = "applications/calendar_print.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        from postulo.documents import paper as papers
        from postulo.documents import themes

        asked = agenda.asked_for(request.GET)
        context = calendar_print.as_context(request.user, asked)
        theme = calendar_print.theme_for(request.user)
        query = _question(request)
        page = reverse("applications:calendar")
        return render(
            request,
            self.template_name,
            {
                "print": context,
                "query": query,
                "back": f"{page}?{query}" if query else page,
                "theme_choices": themes.choices_for(themes.Kind.CALENDAR),
                "chosen_theme": theme,
                "language_choices": calendar_print.offered_languages(),
                "chosen_language": calendar_print.language_for(request.user),
                "paper_choices": papers.choices(papers.offered(theme, themes.Kind.CALENDAR)),
                "chosen_paper": calendar_print.paper_for(theme, request.user, asked.view).key,
                "paper_restriction": papers.restriction(theme, themes.Kind.CALENDAR),
            },
        )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CalendarPrintFileView(LoginRequiredMixin, View):
    """The print itself: a PDF, handed back and never filed (`calendar_print`).

    The period is this address's query; the choices for the document are the form's on a
    POST and the query's on a GET. The POST is the button on the print page, and remembers
    the theme, so the next calendar is set the same way; a GET -- a link, a bookmark --
    writes nothing.

    **Outside a transaction of its own**, as a list's export is (#220): the period is read
    and then a renderer draws it, and on SQLite a request held in a transaction holds the
    write lock for all of it, to write nothing.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        return self._answer(request, request.GET)

    def post(self, request: HttpRequest) -> HttpResponse:
        return self._answer(request, request.POST, remember=True)

    def _back(self, request: HttpRequest, said) -> HttpResponse:
        messages.error(request, str(said))
        query = _question(request)
        target = reverse("applications:calendar_print")
        return redirect(f"{target}?{query}" if query else target)

    def _answer(self, request: HttpRequest, choices, *, remember: bool = False):
        from postulo.documents import themes
        from postulo.documents.pdf import PDFBackendUnavailable, draft_pdf

        asked = choices.get("theme", "")
        try:
            html, theme, _language = calendar_print.draw(
                request,
                theme=asked,
                language=choices.get("language", ""),
                paper=choices.get("paper", ""),
            )
        except themes.CannotRender as refused:
            # A paper the theme does not print on (#344).
            return self._back(request, refused)
        try:
            pdf = draft_pdf(html)
        except PDFBackendUnavailable as unavailable:
            return self._back(request, unavailable)
        if remember and asked == theme:
            # Only a theme the form offered, as on the report's page (#340).
            themes.remember(getattr(request.user, "profile", None), themes.Kind.CALENDAR, theme)
        response = HttpResponse(pdf, content_type="application/pdf")
        name = calendar_print.filename(agenda.asked_for(request.GET))
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
