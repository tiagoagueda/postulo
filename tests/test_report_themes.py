"""A theme sets the job-search report, as it sets a CV or a letter (#340).

> templates for job searching reports

The report was drawn from one fixed template, so a plugin offering report templates had
nowhere to put one. It has a theme kind now, `ThemeKind.REPORT`, and what a report theme is
handed is plain values -- the contract written in the wiki's *Writing a plugin*.

What is held here: that somebody who never chooses a theme is handed the report they always
were; that a plugin's report theme is offered on the report's page, draws both the PDF the
address hands back and the one the press files, and is remembered for that person and nobody
else; that a theme is handed no model; that the report is drawn in the language chosen for it,
by the page and the worker alike; that a filed document says which theme drew it; and that the
choice and the record travel in the account archive.
"""

from __future__ import annotations

import datetime as dt
import sys
import zipfile
from dataclasses import is_dataclass

import pytest
from django.db.models import Model
from django.urls import reverse

from postulo.applications import reports
from postulo.documents import kinds, themes
from postulo.documents.models import DocumentKind, RenderedDocument
from tests.test_reports import march, sent

pytestmark = pytest.mark.django_db

PAGE = "applications:report"
PDF = "applications:report_pdf"


@pytest.fixture(autouse=True)
def _no_plugin_themes():
    """Nothing a test registers survives into the next one."""
    yield
    themes.forget()


@pytest.fixture
def drawn(monkeypatch):
    """A renderer that draws nothing, so these run where WeasyPrint is not installed."""
    monkeypatch.setattr(
        "postulo.documents.rendering.html_to_pdf", lambda html, backend=None: b"%PDF-1.7 fake"
    )


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A plugin, Ledger, whose one theme sets reports and nothing else.

    Its template prints what it is handed, so a test can read back what a theme from outside
    is given and in which language.
    """
    from postulo.plugins import themes as plugin_themes

    package = tmp_path / "ledger"
    (package / "templates" / "ledger").mkdir(parents=True)
    (package / "templates" / "ledger" / "report.html").write_text(
        '<html lang="{{ document_language }}" dir="{{ document_direction }}">'
        "<title>{{ document_title }}</title><body>LEDGER"
        "|{{ person }}|{{ period.label }}|{{ total }}"
        "|{% for row in evidence %}{{ row.company }};{% endfor %}"
        "|{% for row in industries %}{{ row.code }}:{{ row.name }};{% endfor %}"
        "</body></html>",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import Theme, ThemeKind\n"
        "class Ledger:\n"
        "    name = 'ledger'\n"
        "    kind = 'feature'\n"
        "    themes = [Theme(name='ledger', label='Ledger',\n"
        "                    templates={ThemeKind.REPORT: 'ledger/report.html'},\n"
        "                    provider='Ledger Press')]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    before = list(plugin_themes._registered)

    from ledger import Ledger

    assert plugin_themes.register_plugin_themes(Ledger) == 1
    yield themes.find("ledger")
    themes.forget()
    plugin_themes._registered[:] = before
    plugin_themes._forget_the_engines()
    sys.modules.pop("ledger", None)


# ------------------------------------------------------------------ the kind itself


def test_a_report_is_a_kind_a_theme_sets_and_postulos_own_both_set_it():
    assert themes.Kind.REPORT == "report"
    assert kinds.theme_kind_for(DocumentKind.REPORT) == themes.Kind.REPORT
    assert {theme.name for theme in themes.for_kind(themes.Kind.REPORT)} == {"plain", "classic"}


def test_nobody_who_never_chose_is_handed_anything_but_plain(user):
    assert user.profile.document_themes == {}
    assert reports.theme_for(user) == themes.DEFAULT == "plain"


def test_the_built_in_report_theme_draws_the_report_as_it_was(user):
    """Today's report moved into `plain`, so nothing changed for somebody who never picks."""
    sent(user, on=dt.date(2026, 3, 4), company="Aperture Science")

    _report, html, theme, _language = reports.draw(user, march())

    assert theme == "plain"
    assert themes.template_for("plain", themes.Kind.REPORT) == "documents/themes/plain/report.html"
    for said in (
        "Job search report",
        user.display_name,
        "size: A4",
        "counter(page)",
        '"DejaVu Sans"',
        "How regularly",
        "Every application, in order",
        "Aperture Science",
        "Produced by Postulo from this person",
    ):
        assert said in html, said
    assert "<title>Job search report · " in html


def test_classic_sets_a_report_too(user):
    _report, html, theme, _language = reports.draw(user, march(), theme="classic")

    assert theme == "classic"
    assert "Palatino" in html and "Job search report" in html


# ------------------------------------------------------------- a plugin's theme


def test_a_plugins_report_theme_is_offered_on_the_page(client, user, ledger):
    client.force_login(user)

    page = client.get(reverse(PAGE)).content.decode()

    assert '<option value="ledger"' in page
    assert "Ledger, from Ledger Press" in page
    assert '<option value="plain" selected' in page, "the default until somebody chooses"


def test_the_press_draws_in_the_chosen_theme_files_it_and_remembers_it(client, user, ledger, drawn):
    sent(user, on=dt.date(2026, 3, 4), company="Black Mesa")
    client.force_login(user)

    client.post(
        reverse(PDF) + "?period=month&on=2026-03", {"theme": "ledger", "language": "en-GB"}
    ).close()

    filed = RenderedDocument.objects.for_user(user).get()
    assert filed.source_text.count("LEDGER") == 1, "the filed PDF is the plugin's"
    assert "Black Mesa;" in filed.source_text
    assert filed.theme == "ledger", "the filed document says which theme drew it"
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {"report": "ledger"}

    page = client.get(reverse(PAGE)).content.decode()
    assert '<option value="ledger" selected' in page, "remembered, so offered first next time"


def test_the_address_draws_in_the_remembered_theme_and_remembers_nothing(
    client, user, ledger, monkeypatch
):
    seen: list[str] = []

    def draft(html, *args, **kwargs):
        seen.append(html)
        return b"%PDF-"

    monkeypatch.setattr("postulo.documents.pdf.draft_pdf", draft)
    themes.remember(user.profile, themes.Kind.REPORT, "ledger")
    client.force_login(user)

    client.get(reverse(PDF)).close()
    client.get(reverse(PDF), {"theme": "classic"}).close()

    assert "LEDGER" in seen[0], "the GET hands back what the press would file"
    assert "Palatino" in seen[1], "an address may ask for another"
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {"report": "ledger"}, "a GET writes nothing"


def test_the_choice_is_this_persons_and_nobody_elses(client, user, other_user, ledger, drawn):
    client.force_login(user)
    client.post(reverse(PDF), {"theme": "ledger"}).close()

    other_user.profile.refresh_from_db()
    user.profile.refresh_from_db()
    assert reports.theme_for(other_user) == "plain"
    assert reports.theme_for(user) == "ledger"


def test_a_theme_that_does_not_set_reports_is_neither_used_nor_remembered(client, user, drawn):
    themes.register(
        themes.Theme(
            name="vellum",
            label="Vellum",
            templates={themes.Kind.LETTER: "documents/themes/plain/letter.html"},
        )
    )
    client.force_login(user)

    assert '<option value="vellum"' not in client.get(reverse(PAGE)).content.decode()
    client.post(reverse(PDF), {"theme": "vellum"}).close()

    user.profile.refresh_from_db()
    assert user.profile.document_themes == {}
    assert RenderedDocument.objects.for_user(user).get().theme == "plain"


def test_a_remembered_theme_whose_plugin_went_away_draws_in_plain(user):
    user.profile.document_themes = {"report": "gone"}
    user.profile.save()

    _report, html, theme, _language = reports.draw(user, march())

    assert theme == "plain" and "DejaVu Sans" in html


# ------------------------------------------------------------- what a theme is handed


def leaves(value, path="context"):
    """Every value the context holds, with where it is, however deep."""
    if isinstance(value, dict):
        for key, inner in value.items():
            assert isinstance(key, str), f"{path} has a key that is not a name: {key!r}"
            yield from leaves(inner, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, inner in enumerate(value):
            yield from leaves(inner, f"{path}[{index}]")
    else:
        yield path, value


def test_a_report_theme_is_handed_plain_values_only(user):
    """No model and no dataclass of Postulo's: what a theme reads is the contract, and
    nothing it is handed can reach the database."""
    from postulo.jobs.models import Company, CompanyKind, Contact, Industry

    application = sent(user, on=dt.date(2026, 3, 4))
    industry, _made = Industry.objects.get_or_create(owner=user, name="Software")
    application.posting.company.industries.add(industry)
    office = Company.objects.create(
        owner=user, name="France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    Contact.objects.create(owner=user, company=office, name="Camille Durand")

    context = reports.as_context(reports.build(user, march(), today=dt.date(2026, 3, 31)))

    plain = (str, int, float, bool, type(None), dt.date, dt.datetime)
    for path, value in leaves(context):
        assert not isinstance(value, Model), path
        assert not is_dataclass(value), path
        assert isinstance(value, plain), f"{path} is a {type(value).__name__}"
    assert {
        "person",
        "office",
        "adviser",
        "produced_at",
        "period",
        "total",
        "cadence",
        "happened",
        "evidence",
        "sources",
        "industries",
    } <= set(context)
    assert context["office"] == "France Travail" and context["adviser"] == "Camille Durand"
    assert context["industries"][0]["code"] == "K"
    assert context["industries"][0]["children"] == [{"name": "Software", "count": 1}]
    assert set(context["evidence"][0]) == {
        "applied_on",
        "company",
        "role",
        "source",
        "url",
        "status",
        "last_activity_on",
    }, "who referred somebody is not a theme's to print (#239)"


def test_a_theme_from_outside_reads_the_industries_and_the_rest(user, ledger):
    from postulo.jobs.models import Industry

    application = sent(user, on=dt.date(2026, 3, 4), company="Initech")
    industry, _made = Industry.objects.get_or_create(owner=user, name="Software")
    application.posting.company.industries.add(industry)

    _report, html, theme, _language = reports.draw(
        user, reports.month_period(2026, 3), theme="ledger"
    )

    assert theme == "ledger"
    assert f"|{user.display_name}|" in html
    assert "|1|Initech;|" in html
    assert "K:" in html, "the industries are in the context, for a theme to print or not"


# ------------------------------------------------------------------------ language


def test_the_report_is_drawn_in_the_language_chosen_for_it_whoever_draws_it(
    client, user, ledger, drawn, monkeypatch
):
    """The page's GET and the worker's errand draw the same language for the same choice."""
    seen: list[str] = []
    monkeypatch.setattr(
        "postulo.documents.pdf.draft_pdf", lambda html, *a, **k: seen.append(html) or b"%PDF-"
    )
    client.force_login(user)
    query = "?period=month&on=2026-03"

    client.get(
        reverse(PDF),
        {"period": "month", "on": "2026-03", "theme": "ledger", "language": "fr-FR"},
    ).close()
    client.post(reverse(PDF) + query, {"theme": "ledger", "language": "fr-FR"}).close()

    filed = RenderedDocument.objects.for_user(user).get()
    assert filed.language == "fr-FR"
    for html in (seen[0], filed.source_text):
        assert '<html lang="fr-FR" dir="ltr">' in html
        assert "|mars 2026|" in html, "the figures' labels are in it too, not only the root"


def test_the_language_starts_where_a_cvs_does(client, user):
    user.profile.language = "pt-PT"
    user.profile.save()
    client.force_login(user)

    assert reports.language_for(user) == "pt-PT"
    assert reports.language_for(user, "not a tag!") == "pt-PT"
    assert reports.language_for(user, "fr-FR") == "fr-FR"
    page = client.get(reverse(PAGE)).content.decode()
    assert '<option value="pt-PT" lang="pt-PT" selected' in page


# ----------------------------------------------------------- the filed document


def test_a_filed_cv_and_letter_record_the_theme_that_drew_them(user, drawn):
    from postulo.documents.models import CV, CoverLetter
    from postulo.documents.rendering import snapshot_cv, snapshot_letter

    cv = CV.objects.create(owner=user, name="Main", theme="classic")
    letter = CoverLetter.objects.create(owner=user, name="General", body="Dear team.")
    # A name nothing answers to any more is drawn by `plain`, and that is what is recorded.
    CoverLetter.objects.filter(pk=letter.pk).update(theme="gone")
    letter.refresh_from_db()

    assert snapshot_cv(cv).theme == "classic"
    assert snapshot_letter(letter).theme == "plain"


# ---------------------------------------------------------------------- the archive


def test_the_archive_carries_the_choice_and_what_drew_each_file(user, other_user, drawn):
    from postulo.core import export, importer

    user.profile.document_themes = {"report": "classic"}
    user.profile.save()
    reports_filed = RenderedDocument.objects.for_user(user)
    from postulo.documents.rendering import snapshot_report

    _report, html, theme, language = reports.draw(user, march())
    snapshot_report(
        user, title="A report", html=html, filename="r.pdf", theme=theme, language=language
    )
    assert reports_filed.get().theme == "classic"

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    other_user.profile.refresh_from_db()
    assert other_user.profile.document_themes == {"report": "classic"}
    assert RenderedDocument.objects.for_user(other_user).get().theme == "classic"


def test_what_an_archive_says_about_themes_is_not_simply_believed(user):
    import io
    import json

    from postulo.core import export, importer

    document = export.build_document(user)
    document["account"]["profile"]["document_themes"] = {
        "report": "classic",
        "nonsense": "classic",
        "letter": "x" * 500,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))

    importer.load(user, zipfile.ZipFile(buffer), force=True)

    user.profile.refresh_from_db()
    assert user.profile.document_themes == {"report": "classic"}


def test_the_format_moved_and_says_why():
    from pathlib import Path

    from postulo.core import export

    assert "document_themes" in export.PROFILE_FIELDS
    assert "document_themes" not in export.CANDIDATE_PROFILE_FIELDS
    assert "theme" in export.SENT_FIELDS
    assert export.FORMAT_VERSION >= 77
    source = Path(export.__file__).read_text(encoding="utf-8")
    assert "added ``document_themes`` on the profile" in source
