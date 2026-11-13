"""One interview's page, and the PDF of it (#1091).

`interview_brief` says what the page and the document hold and why; this is the two
addresses around it, shaped as the calendar's print is (#342).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from . import interview_brief
from .models import Interview


def _interview_for(request: HttpRequest, pk: int) -> Interview:
    """One of the reader's own, narrowed before it is looked up, so another person's is a 404."""
    return get_object_or_404(
        Interview.objects.for_user(request.user)
        .select_related("application", "application__posting", "application__posting__company")
        .prefetch_related("contacts"),
        pk=pk,
    )


class InterviewDetailView(LoginRequiredMixin, View):
    """What to prepare from before this interview, gathered, and the choices for printing it."""

    template_name = "applications/interview_detail.html"

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.documents import paper as papers
        from postulo.documents import themes

        interview = _interview_for(request, pk)
        theme = interview_brief.theme_for(request.user)
        return render(
            request,
            self.template_name,
            {
                "interview": interview,
                "application": interview.application,
                "brief": interview_brief.as_context(request.user, interview),
                "theme_choices": themes.choices_for(themes.Kind.INTERVIEW),
                "chosen_theme": theme,
                "language_choices": interview_brief.offered_languages(),
                "chosen_language": interview_brief.language_for(request.user),
                "paper_choices": papers.choices(papers.offered(theme, themes.Kind.INTERVIEW)),
                "chosen_paper": interview_brief.paper_for(theme, request.user).key,
                "paper_restriction": papers.restriction(theme, themes.Kind.INTERVIEW),
            },
        )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class InterviewPDFView(LoginRequiredMixin, View):
    """The page as a PDF, handed back and never filed (`interview_brief`).

    The choices for the document are the form's on a POST and the query's on a GET. The POST
    is the button on the page, and remembers the theme, so the next one is set the same way;
    a GET -- a link, a bookmark -- writes nothing.

    **Outside a transaction of its own**, as a list's export and the calendar's print are
    (#220): the page is read and then a renderer draws it, and on SQLite a request held in a
    transaction holds the write lock for all of it, to write nothing.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        return self._answer(request, pk, request.GET)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        return self._answer(request, pk, request.POST, remember=True)

    def _back(self, request: HttpRequest, pk: int, said) -> HttpResponse:
        messages.error(request, str(said))
        return redirect(reverse("applications:interview_detail", args=[pk]))

    def _answer(self, request: HttpRequest, pk: int, choices, *, remember: bool = False):
        from postulo.documents import themes
        from postulo.documents.pdf import PDFBackendUnavailable, draft_pdf

        interview = _interview_for(request, pk)
        asked = choices.get("theme", "")
        try:
            html, theme, _language = interview_brief.draw(
                request,
                interview,
                theme=asked,
                language=choices.get("language", ""),
                paper=choices.get("paper", ""),
            )
        except themes.CannotRender as refused:
            # A paper the theme does not print on (#344).
            return self._back(request, pk, refused)
        try:
            pdf = draft_pdf(html)
        except PDFBackendUnavailable as unavailable:
            return self._back(request, pk, unavailable)
        if remember and asked == theme:
            # Only a theme the form offered, as on the report's page (#340).
            themes.remember(getattr(request.user, "profile", None), themes.Kind.INTERVIEW, theme)
        response = HttpResponse(pdf, content_type="application/pdf")
        name = interview_brief.filename(interview)
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
