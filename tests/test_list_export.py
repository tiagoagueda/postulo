"""A table exported whole, as a themed PDF or a CSV of the same rows (#341).

> templates for exporting lists, either company lists, job applications lists, or other lists

Postulo could not export a list: printing one was the browser's dialog over one page of
rows. What is held here: that each of the three tables exports every row it answers with,
whole or as filtered, in the person's columns and order and in the sort in the address; that
nobody else's records reach it; that the CSV says what the table says, with every cell
guarded; that a theme sets the PDF -- Postulo's own, or a plugin's that sets the list kind --
and is remembered; that the paper is the person's, on its side; and that the ceiling is said.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import sys

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Priority, Status
from postulo.core import list_export, tables
from postulo.documents import themes
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_plugin_themes():
    yield
    themes.forget()


@pytest.fixture
def drafts(monkeypatch):
    """What the renderer was handed, and a PDF that is not one, so these run without it."""
    seen: list[str] = []

    def draft(html, *args, **kwargs):
        seen.append(html)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr("postulo.documents.pdf.draft_pdf", draft)
    return seen


def applied(user, role, company, *, on, status=Status.APPLIED, priority=Priority.NORMAL):
    firm, _made = Company.objects.get_or_create(owner=user, name=company)
    posting = JobPosting.objects.create(owner=user, company=firm, title=role)
    when = timezone.make_aware(dt.datetime.combine(on, dt.time(10, 0)))
    return Application.objects.create(
        owner=user, posting=posting, status=status, applied_at=when, priority=priority
    )


@pytest.fixture
def three(user):
    applied(user, "Backend Engineer", "Aperture", on=dt.date(2026, 3, 4))
    applied(user, "Data Engineer", "Black Mesa", on=dt.date(2026, 3, 10))
    applied(user, "Platform Engineer", "Initech", on=dt.date(2026, 2, 1), status=Status.REJECTED)


def export_csv(client, name, query=""):
    response = client.get(
        reverse("core:table_export_file", args=[name]) + f"?format=csv&language=en-GB&{query}"
    )
    assert response.status_code == 200, response.status_code
    assert response["Content-Type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(response.content.decode())))


# ------------------------------------------------------------------- the three tables


def test_the_three_tables_are_exportable_and_no_other():
    assert {name for name in tables.TABLES if list_export.exportable(name)} >= {
        "applications",
        "companies",
        "listings",
    }
    assert list_export.exportable("nonsense") is None


def test_applications_export_whole_in_the_usual_columns(client, user, three):
    client.force_login(user)

    rows = export_csv(client, "applications")

    assert rows[0] == ["Role", "Company", "Location", "Status", "Applied"]
    assert len(rows) == 4, "every row, settled ones included"
    assert rows[1][:2] == ["Platform Engineer", "Initech"], "last recorded first, as the table"
    assert {row[3] for row in rows[1:]} == {"Applied", "Rejected"}


def test_applications_export_as_filtered_and_sorted(client, user, three):
    client.force_login(user)

    rows = export_csv(client, "applications", "status=applied&sort=role")

    assert [row[0] for row in rows[1:]] == ["Backend Engineer", "Data Engineer"]


def test_the_persons_own_columns_in_their_order(client, user, three):
    tables.save_settings(
        user, "applications", {"columns": ["company", "priority", "role"], "page_size": 25}
    )
    client.force_login(user)

    rows = export_csv(client, "applications", "sort=company")

    assert rows[0] == ["Company", "Priority", "Role"]
    assert rows[1] == ["Aperture", "Normal", "Backend Engineer"]


def test_every_row_not_the_page_on_screen(client, user):
    for number in range(30):
        applied(user, f"Role {number}", "Aperture", on=dt.date(2026, 3, 1))
    tables.save_settings(user, "applications", {"columns": ["role"], "page_size": 25})
    client.force_login(user)

    assert len(export_csv(client, "applications")) == 31


def test_the_table_not_the_board_whatever_was_chosen(client, user, three):
    tables.save_settings(user, "applications", {"shape": "board"})
    client.force_login(user)

    rows = export_csv(client, "applications")

    assert len(rows) == 4, "the board leaves settled applications out; a list cannot"


def test_companies_export_whole_and_filtered(client, user):
    Company.objects.create(owner=user, name="Aperture", website="https://aperture.example")
    Company.objects.create(owner=user, name="Black Mesa")
    tables.save_settings(user, "companies", {"columns": ["name", "website", "postings"]})
    client.force_login(user)

    whole = export_csv(client, "companies")
    narrowed = export_csv(client, "companies", "name=mesa")

    assert whole[0] == ["Name", "Website", "Postings"]
    assert whole[1:] == [["Aperture", "https://aperture.example", "0"], ["Black Mesa", "", "0"]]
    assert narrowed[1:] == [["Black Mesa", "", "0"]]


def test_listings_export_the_tab_and_the_filters(client, user):
    firm = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=firm, title="Tester", location="Lisbon")
    JobPosting.objects.create(owner=user, company=firm, title="Cook", location="Porto")
    tables.save_settings(user, "listings", {"columns": ["title", "location"]})
    client.force_login(user)

    rows = export_csv(client, "listings", "location=porto")

    assert rows == [["Role", "Location"], ["Cook", "Porto"]]


# ---------------------------------------------------------------- whose records


@pytest.mark.parametrize("name", ["applications", "companies", "listings"])
def test_nobody_elses_records_are_exported(client, user, other_user, name):
    """The page's own scoping, asked once more by the same person (#896)."""
    applied(other_user, "Their Role", "Their Company", on=dt.date(2026, 3, 1))
    applied(user, "My Role", "My Company", on=dt.date(2026, 3, 1))
    client.force_login(user)

    text = client.get(
        reverse("core:table_export_file", args=[name]) + "?format=csv&state=all"
    ).content.decode()
    page = client.get(reverse("core:table_export", args=[name]) + "?state=all").content.decode()

    assert "Their" not in text and "Their" not in page
    assert "My " in text


def test_a_table_nothing_exports_is_a_404(client, user):
    client.force_login(user)

    assert client.get(reverse("core:table_export", args=["nonsense"])).status_code == 404
    assert client.get(reverse("core:table_export_file", args=["nonsense"])).status_code == 404


def test_signed_out_is_sent_to_sign_in(client):
    response = client.get(reverse("core:table_export_file", args=["applications"]))

    assert response.status_code == 302 and "login" in response["Location"]


# ----------------------------------------------------------------------- the CSV


def test_the_csv_says_what_the_table_says(client, user, three):
    """The same cells, in the same order, as the table's rows: one source for both."""
    client.force_login(user)
    page = client.get(reverse("applications:list")).content.decode()

    rows = export_csv(client, "applications")

    for row in rows[1:]:
        for cell in row:
            assert cell in page, cell


def test_a_cell_a_spreadsheet_would_run_is_guarded(client, user):
    applied(user, '=HYPERLINK("https://evil.example","Open")', "Aperture", on=dt.date(2026, 3, 1))
    client.force_login(user)

    rows = export_csv(client, "applications")

    assert rows[1][0].startswith("'="), "#218: text, not a formula"


def test_the_csv_is_written_in_the_language_asked_for(client, user, three):
    client.force_login(user)

    response = client.get(
        reverse("core:table_export_file", args=["applications"]) + "?format=csv&language=fr-FR"
    )

    assert "Entreprise" in response.content.decode().splitlines()[0]


# ----------------------------------------------------------------------- the PDF


def test_the_pdf_through_the_built_in_theme(client, user, three, drafts):
    client.force_login(user)

    response = client.post(
        reverse("core:table_export_file", args=["applications"]) + "?status=applied",
        {"format": "pdf", "theme": "plain", "language": "en-GB"},
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    html = drafts[0]
    for said in ("<h1><bdi>Applications</bdi></h1>", "Backend Engineer", "Status: Applied"):
        assert said in html, said
    assert "Platform Engineer" not in html
    assert '"DejaVu Sans"' in html
    assert "landscape" in html, "a list is drawn on its side unless somebody chose otherwise"


def test_classic_sets_a_list_too(client, user, three, drafts):
    client.force_login(user)

    client.post(
        reverse("core:table_export_file", args=["companies"]), {"format": "pdf", "theme": "classic"}
    )

    assert "Palatino" in drafts[0] and "Aperture" in drafts[0]


def test_the_chosen_paper_is_the_one_drawn(client, user, three, drafts):
    client.force_login(user)

    client.post(
        reverse("core:table_export_file", args=["applications"]),
        {"format": "pdf", "paper": "letter-portrait"},
    )

    assert "size: letter portrait" in drafts[0]


@pytest.fixture
def roster(tmp_path, monkeypatch):
    """A plugin, Roster, whose one theme sets lists and nothing else, printing what it is
    handed so a test can read it back."""
    from postulo.plugins import themes as plugin_themes

    package = tmp_path / "roster"
    (package / "templates" / "roster").mkdir(parents=True)
    (package / "templates" / "roster" / "list.html").write_text(
        '<html lang="{{ document_language }}"><title>{{ document_title }}</title><body>ROSTER'
        "|{{ list }}|{{ title }}|{{ total }}"
        "|{% for column in columns %}{{ column.label }};{% endfor %}"
        "|{% for row in rows %}{{ row|join:',' }};{% endfor %}"
        "|{% for filter in filters %}{{ filter.label }}={{ filter.value }};{% endfor %}"
        "|{{ sort.words }}</body></html>",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import Theme, ThemeKind\n"
        "class Roster:\n"
        "    name = 'roster'\n"
        "    kind = 'feature'\n"
        "    themes = [Theme(name='roster', label='Roster',\n"
        "                    templates={ThemeKind.LIST: 'roster/list.html'},\n"
        "                    provider='Roster Press')]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    before = list(plugin_themes._registered)

    from roster import Roster

    assert plugin_themes.register_plugin_themes(Roster) == 1
    yield themes.find("roster")
    themes.forget()
    plugin_themes._registered[:] = before
    plugin_themes._forget_the_engines()
    sys.modules.pop("roster", None)


def test_a_plugins_list_theme_is_offered_draws_and_is_remembered(
    client, user, three, roster, drafts
):
    client.force_login(user)
    page = reverse("core:table_export", args=["applications"])

    assert "Roster, from Roster Press" in client.get(page).content.decode()
    client.post(
        reverse("core:table_export_file", args=["applications"]) + "?status=applied&sort=role",
        {"format": "pdf", "theme": "roster", "language": "fr-FR"},
    )

    html = drafts[0]
    assert "ROSTER|applications|Candidatures|2|" in html
    assert "Backend Engineer,Aperture" in html
    assert "Statut=" in html, "the filters in words, in the document's language"
    assert "Poste;" in html, "the column labels too"
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {"list": "roster"}
    assert '<option value="roster" selected' in client.get(page).content.decode()


def test_a_get_draws_and_remembers_nothing(client, user, three, roster, drafts):
    client.force_login(user)

    client.get(reverse("core:table_export_file", args=["applications"]), {"theme": "roster"})

    assert "ROSTER" in drafts[0]
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {}


def test_a_list_theme_is_handed_plain_values_only(user, three, rf):
    from dataclasses import is_dataclass

    from django.db.models import Model

    request = rf.get("/", {"status": "applied"})
    request.user = user
    opened = list_export.open_table(request, "applications")

    context = list_export.as_context(opened, user, limit=10)

    def leaves(value):
        if isinstance(value, dict):
            for inner in value.values():
                yield from leaves(inner)
        elif isinstance(value, list | tuple):
            for inner in value:
                yield from leaves(inner)
        else:
            yield value

    for value in leaves(context):
        assert not isinstance(value, Model) and not is_dataclass(value)
        assert isinstance(value, str | int | bool | dt.datetime), type(value)
    assert {
        "list",
        "title",
        "person",
        "produced_at",
        "filters",
        "sort",
        "columns",
        "rows",
        "count",
        "total",
        "limit",
        "truncated",
        "left_out",
    } == set(context)


def test_the_ceiling_is_said_on_the_page_and_in_the_document(client, user, monkeypatch, drafts):
    monkeypatch.setattr(list_export, "PDF_LIMIT", 2)
    for number in range(3):
        applied(user, f"Role {number}", "Aperture", on=dt.date(2026, 3, 1))
    client.force_login(user)

    page = client.get(reverse("core:table_export", args=["applications"])).content.decode()
    client.post(reverse("core:table_export_file", args=["applications"]), {"format": "pdf"})

    assert "A PDF holds the first 2 of them" in page
    assert "The first 2 of 3 rows" in drafts[0]


# ----------------------------------------------------------------------- the page


def test_the_button_on_each_table_leads_to_the_export_with_its_question(client, user, three):
    client.force_login(user)

    for page in ("applications:list", "jobs:company_list", "listings:list"):
        assert "data-table-export" in client.get(reverse(page) + "?state=all").content.decode()
    response = client.get(
        reverse("core:table_export", args=["applications"]),
        {"from": "https://evil.example/applications/?status=applied&page=2"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/tables/applications/export/?status=applied"


def test_the_page_says_what_the_export_will_hold(client, user, three):
    client.force_login(user)

    page = client.get(
        reverse("core:table_export", args=["applications"]) + "?status=applied&sort=role"
    ).content.decode()

    assert "2 rows match." in page
    assert "Role, Company, Location, Status, Applied" in page
    assert "Status: Applied" in page
    assert "Sorted by Role, lowest first" in page
    assert '<option value="a4-landscape" selected' in page


@pytest.mark.parametrize("name", ["applications", "companies", "listings"])
def test_every_column_a_table_has_writes_its_cell(client, user, name):
    """No column of the three is left to say nothing: each one the page can show, the
    export can write, in the order chosen."""
    firm = Company.objects.create(owner=user, name="Aperture", website="https://a.example")
    posting = JobPosting.objects.create(
        owner=user, company=firm, title="Tester", location="Lisbon", url="https://a.example/1"
    )
    Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    table = tables.TABLES[name]
    keys = [column.key for column in table.columns]
    tables.save_settings(user, name, {"columns": keys})
    client.force_login(user)

    rows = export_csv(client, name, "state=all")

    assert rows[0] == [str(column.label) for column in table.columns]
    assert len(rows) == 2 and len(rows[1]) == len(keys)
    assert "Aperture" in rows[1]
    assert not [column.label for column in table.columns if not column.exported]
