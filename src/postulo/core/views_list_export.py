"""Exporting a table: the page that says what will be exported, and the file (#341).

`list_export` says what an export is and why it is what it is; this is the two addresses
around it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from . import list_export


def _without(query: QueryDict, *names: str) -> str:
    """``query`` as an address's query string, less ``names``."""
    kept = query.copy()
    for name in names:
        kept.pop(name, None)
    return kept.urlencode()


class TableExportView(LoginRequiredMixin, View):
    """What an export of this table will hold, and the choices for the document.

    Reached from the *Export* button above a table, which sends the address the table is at
    as ``from`` (#623's rule: the address bar is the table on screen, and a filter changed
    since the page loaded is in it). That address's question is taken and this page answers
    at its own address with it, so the page can be reloaded, bookmarked and shared like the
    table. Only the query is read from ``from``: the path it names is never followed.
    """

    template_name = "core/table_export.html"

    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        from postulo.documents import paper as papers
        from postulo.documents import themes

        if "from" in request.GET:
            asked = QueryDict(urlsplit(request.GET.get("from", "")).query)
            query = _without(asked, "page", "from")
            return redirect(f"{request.path}?{query}" if query else request.path)

        opened = list_export.open_table(request, name)
        table = opened.table
        theme = list_export.theme_for(request.user)
        query = _without(request.GET, "page")
        total = opened.rows.count()
        page = reverse(table.page_name)
        return render(
            request,
            self.template_name,
            {
                "table": table,
                "query": query,
                "back": f"{page}?{query}" if query else page,
                "total": total,
                "pdf_limit": list_export.PDF_LIMIT,
                "csv_limit": list_export.CSV_LIMIT,
                "over_pdf": total > list_export.PDF_LIMIT,
                "over_csv": total > list_export.CSV_LIMIT,
                "filters": table.in_words(),
                "sort": table.sort_in_words(),
                "columns": [str(column.label) for column in table.exported_columns],
                "left_out": [str(column.label) for column in table.left_out],
                "theme_choices": themes.choices_for(themes.Kind.LIST),
                "chosen_theme": theme,
                "language_choices": list_export.offered_languages(),
                "chosen_language": list_export.language_for(request.user),
                "paper_choices": papers.choices(papers.offered(theme, themes.Kind.LIST)),
                "chosen_paper": list_export.paper_for(theme, request.user).key,
                "paper_restriction": papers.restriction(theme, themes.Kind.LIST),
            },
        )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class TableExportFileView(LoginRequiredMixin, View):
    """The export itself: a PDF, or with ``format=csv`` a spreadsheet, handed back.

    **A download, never filed** (`list_export`). The table's question is this address's
    query; the choices for the document are the form's on a POST and the query's on a GET.
    The POST is the button on the export page, and remembers the theme it drew a PDF in, so
    the next list is set the same way; a GET -- a link, a bookmark -- writes nothing.

    **Outside a transaction of its own**, as the report's PDF is (#220): every row of a
    table is read and then a renderer draws them, and on SQLite a request held in a
    transaction holds the write lock for all of it, to write nothing.
    """

    def _choices(self, request: HttpRequest) -> QueryDict:
        return request.POST if request.method == "POST" else request.GET

    def _back(self, request: HttpRequest, name: str, said) -> HttpResponse:
        messages.error(request, str(said))
        query = _without(request.GET, "format", "theme", "language", "paper")
        target = reverse("core:table_export", args=[name])
        return redirect(f"{target}?{query}" if query else target)

    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        return self._answer(request, name)

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        return self._answer(request, name, remember=True)

    def _answer(self, request: HttpRequest, name: str, *, remember: bool = False):
        from postulo.documents import themes
        from postulo.documents.pdf import PDFBackendUnavailable, draft_pdf

        choices = self._choices(request)
        list_export.open_table(request, name)  # a 404 for a table nothing exports, first
        if choices.get("format") == "csv":
            text = list_export.as_csv(request, name, language=choices.get("language", ""))
            response = HttpResponse(text, content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = (
                f'attachment; filename="{list_export.filename(name, "csv")}"'
            )
            return response

        asked = choices.get("theme", "")
        try:
            html, theme, _language = list_export.draw(
                request,
                name,
                theme=asked,
                language=choices.get("language", ""),
                paper=choices.get("paper", ""),
            )
        except themes.CannotRender as refused:
            # A paper the theme does not print on (#344).
            return self._back(request, name, refused)
        try:
            pdf = draft_pdf(html)
        except PDFBackendUnavailable as unavailable:
            return self._back(request, name, unavailable)
        if remember and asked == theme:
            # Only a theme the form offered, as on the report's page (#340).
            themes.remember(getattr(request.user, "profile", None), themes.Kind.LIST, theme)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{list_export.filename(name, "pdf")}"'
        )
        return response
