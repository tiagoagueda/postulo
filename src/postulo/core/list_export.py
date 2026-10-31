"""A table, exported whole: a PDF set by a theme, or a CSV of the same rows (#341).

Postulo could not export a list. Printing one was the browser's dialog over the page on
screen: one page of rows, with the filter bar, the buttons and the pagination printed too.
The templates plugin was asked for "templates for exporting lists" and there was nothing for
a template to set.

**What is exported is the table as the person has it at that moment.** Their columns, in
their order; the filters and the sort in the address; a saved view's columns where the
address names one -- the same `Table` the page draws, over **every** row it answers with
rather than the page of them on screen. The rows come from the page's own view
(`Table.rows_from`, whose `table_rows()` is the queryset the page paginates), so an export
cannot narrow by something the page does not, or reach a row the page would not show: it is
the page's question asked once more, by the same person, and the page's scoping -- one's
own records and what is shared with one (#896) -- is the export's.

**One theme kind for every list** (`themes.Kind.LIST`), handed plain values: the list's
name, its filters in words, its column labels, the rows as text already written in the
document's language, the count, when it was made, and the language and direction. A table
added later needs nothing new from a theme, because a theme sets the shape of a list and
not any one list.

**A download, never filed.** An export made on its own is not something an employer
received, and #236 keeps those out of the document stores; so the PDF is drawn in the
request, as the report's draft is, and handed back. That is why there is a ceiling
(`PDF_LIMIT`, `CSV_LIMIT`), said on the page before anybody presses anything: a list drawn
in the request has to be one a request can draw.

**Landscape first.** A list is wide, so with nothing chosen it is drawn on the person's
default paper turned on its side, where the theme prints on that (`paper_for`).

**The CSV is the same rows**, through `spreadsheets.row` like every CSV Postulo writes
(#218): a role or a company name is a stranger's text, and a cell beginning ``=`` is a
formula to whoever opens the file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from django.http import Http404
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from . import spreadsheets, tables

#: The most rows a PDF holds. A list of a thousand applications is forty pages on its side,
#: which a renderer draws in seconds; more is a document nobody reads and a request that
#: holds a worker for a minute. The page says so before the button, and the document says
#: how many it left out.
PDF_LIMIT = 1000

#: The most rows a CSV holds. Ten times the PDF's, because a row of text costs nothing to
#: write; still a ceiling, because the rows are read from the database in the request.
CSV_LIMIT = 10000


def exportable(name: str) -> type[tables.Table] | None:
    """The table called ``name``, where it can be exported."""
    table = tables.TABLES.get(name)
    return table if table is not None and table.rows_from else None


@dataclass
class Opened:
    """A table opened for export: the page's own `Table` and the rows it answers with."""

    table: tables.Table
    rows: object

    @property
    def name(self) -> str:
        return self.table.name


def open_table(request, name: str) -> Opened:
    """The table called ``name`` as this request would draw it, every row of it. Raises
    `Http404` for a table nothing exports.

    The page's own view is set up over the request and asked for its rows, so the scoping,
    the filters that are the view's to apply (a tab, a group, a status) and the table's own
    are all the page's, applied in the page's order.
    """
    table_class = exportable(name)
    if table_class is None:
        raise Http404
    view = import_string(table_class.rows_from)()
    view.setup(request)
    return Opened(table=view.table, rows=view.table_rows())


# ---------------------------------------------------------------- what it is drawn in


def offered_languages() -> list[tuple[str, str]]:
    """The languages a list may be written in: the instance's own, each under its own name."""
    from . import languages

    return list(languages.LANGUAGES)


def language_for(user, asked="") -> str:
    """The language a list is written in: the one asked for where it is offered, else the
    one a CV with no language of its own is written in, as for a report (#340)."""
    from types import SimpleNamespace

    from postulo.documents.models import document_language

    from . import languages

    wanted = languages.tag(asked) if languages.well_formed(asked) else ""
    if wanted not in dict(offered_languages()):
        wanted = ""
    return document_language(SimpleNamespace(owner=user, language=wanted))


def theme_for(user, asked="") -> str:
    """The theme a list is set in: the one asked for where it sets lists, else the one this
    person last chose for a list (`themes.remembered`), else `plain`."""
    from postulo.documents import themes

    theme = themes.find(asked) if asked else None
    if theme is not None and theme.sets(themes.Kind.LIST):
        return theme.name
    return themes.remembered(getattr(user, "profile", None), themes.Kind.LIST)


def paper_for(theme: str, user, asked: str = ""):
    """The paper a list is drawn on. Raises `CannotRender` for one asked for that the theme
    does not print on.

    Nothing asked is this person's default paper turned on its side, because a list is
    wide; where the theme does not print on that, the default as any other document takes
    it (`paper_choice.for_document`).
    """
    from postulo.documents import paper, paper_choice, themes

    if asked:
        return paper_choice.for_document(theme, themes.Kind.LIST, user, asked)
    wished = paper_choice.personal_default(user)
    turned = paper.Paper(wished.size, paper.Orientation.LANDSCAPE)
    if turned in paper.offered(theme, themes.Kind.LIST):
        return turned
    return paper_choice.for_document(theme, themes.Kind.LIST, user)


# ------------------------------------------------------------------- the list itself


def as_context(opened: Opened, user, *, limit: int) -> dict:
    """The list as a theme's template is handed it: plain values and nothing else.

    Strings, numbers, booleans, a date and lists and mappings of them -- never a model or
    the table, which are not on the plugin surface. The names are the contract written in
    the wiki's *Writing a plugin*. Built in the language in force, so every label, every
    date and every choice is already written in it; the caller sets that.
    """
    table = opened.table
    columns = table.exported_columns
    total = opened.rows.count()
    rows = [[table.text(row, column) for column in columns] for row in opened.rows[:limit]]
    return {
        "list": table.name,
        "title": str(table.label),
        "person": user.display_name if hasattr(user, "display_name") else str(user),
        "produced_at": timezone.localtime(),
        "filters": table.in_words(),
        "sort": table.sort_in_words(),
        "columns": [
            {"key": column.key, "label": str(column.label), "numeric": column.numeric}
            for column in columns
        ],
        "rows": rows,
        "count": len(rows),
        "total": total,
        "limit": limit,
        "truncated": total > len(rows),
        "left_out": [str(column.label) for column in table.left_out],
    }


def title_of(context: dict) -> str:
    """What the document is called, in the language it is written in."""
    return str(
        _("%(list)s · %(date)s")
        % {"list": context["title"], "date": tables.day_text(context["produced_at"])}
    )


def filename(name: str, suffix: str) -> str:
    """A name that says what the file is and when it was made, without being opened."""
    return f"postulo-{name}-{timezone.localdate():%Y-%m-%d}.{suffix}"


def draw(request, name: str, *, theme: str = "", language: str = "", paper: str = ""):
    """The table called ``name`` set as a document: ``(html, theme, language)``, the last two
    being what it was drawn in. Raises `Http404` and `CannotRender`.

    The paper is checked against the theme before a row is read, so a refusal costs nothing.
    """
    from django.template.loader import render_to_string

    from postulo.documents import paper as papers
    from postulo.documents import theme_assets, themes

    from . import languages

    user = request.user
    theme = theme_for(user, theme)
    language = language_for(user, language)
    chosen = paper_for(theme, user, paper)
    # A file handed to a renderer in this process, never a page a browser is served, so no
    # policy reads it and its styles need no nonce -- as for the report's draft.
    nonce = None
    with languages.override(language):
        context = as_context(open_table(request, name), user, limit=PDF_LIMIT)
        html = render_to_string(
            themes.template_for(theme, themes.Kind.LIST),
            {
                **context,
                "document_language": language,
                "document_direction": languages.direction(language),
                "document_title": title_of(context),
                "csp_nonce": nonce,
                "paper": chosen,
                theme_assets.CONTEXT_KEY: theme_assets.Inliner(themes.find(theme)),
            },
        )
    return papers.with_paper(html, chosen, nonce=nonce), theme, language


def as_csv(request, name: str, *, language: str = "") -> str:
    """The same rows as a spreadsheet: the column labels, then one line per row, every cell
    through `spreadsheets.row` (#218). Written in the language asked for, as the PDF is."""
    from . import languages

    with languages.override(language_for(request.user, language)):
        context = as_context(open_table(request, name), request.user, limit=CSV_LIMIT)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(spreadsheets.row(column["label"] for column in context["columns"]))
        for row in context["rows"]:
            writer.writerow(spreadsheets.row(row))
    return buffer.getvalue()
