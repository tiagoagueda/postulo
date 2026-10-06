"""Bringing a spreadsheet in: read anything Excel writes, guess the columns, import once."""

import datetime as dt
import json
import re
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from postulo.applications.models import Application, ApplicationEvent, EventKind, Status
from postulo.core import csv_import
from postulo.jobs.models import Company, JobPosting, ListingState

pytestmark = pytest.mark.django_db

ENGLISH = (
    "Company,Role,URL,Date applied,Status,Salary,Tags,Notes\n"
    'Aperture Science,Research Engineer,https://aperture.example/jobs/42,2026-09-01,Interviewing,"55,000 - 65,000",remote; dream job,Spoke to Cave first\n'  # noqa: E501
    "Black Mesa,Physicist,,03/09/2026,Rejected,60k,,\n"
    "Initech,Consultant,,,Wishlist,,,Not applied yet\n"
    ",No company here,,2026-09-02,Applied,,,\n"
    "Aperture Science,Research Engineer,https://aperture.example/jobs/42,2026-09-01,Applied,,,duplicate row\n"  # noqa: E501
)

FRENCH = (
    "Entreprise;Poste;Date de candidature;Statut;Lieu\n"
    "Société Générale;Développeur;12/09/2026;Entretien;Paris\n"
    "Vandelay;Import-export;05.09.2026;Sans réponse;Lisbonne\n"
).encode("latin-1")


# --------------------------------------------------------------------- reading


def test_it_reads_a_bom_a_semicolon_and_latin_one():
    sheet = csv_import.read_sheet(("﻿" + ENGLISH).encode("utf-8"), "english.csv")
    assert (
        sheet.headers[0] == "Company" and sheet.delimiter == "," and sheet.encoding == "utf-8-sig"
    )
    assert sheet.row_count == 5

    french = csv_import.read_sheet(FRENCH, "fr.csv")
    assert french.delimiter == ";" and french.encoding == "latin-1"
    assert french.rows[0][0] == "Société Générale"


OVERSIZED = b'Company,Role\nAcme,"' + b"x" * 200000 + b'"\n'


def test_a_field_over_the_reader_limit_is_a_sheet_error():
    with pytest.raises(csv_import.SheetError, match="never closed"):
        csv_import.read_sheet(OVERSIZED)


def test_the_page_answers_an_oversized_field_with_a_message(client, user):
    client.force_login(user)
    response = client.post(
        reverse("core:import_csv"),
        {"file": SimpleUploadedFile("big.csv", OVERSIZED, content_type="text/csv")},
        follow=True,
    )
    assert response.status_code == 200
    assert "never closed" in response.content.decode()


def test_empty_huge_and_rowless_files_are_refused():
    with pytest.raises(csv_import.SheetError, match="empty"):
        csv_import.read_sheet(b"   ")
    with pytest.raises(csv_import.SheetError, match="2 MB"):
        csv_import.read_sheet(b"x" * (csv_import.MAX_BYTES + 1))
    with pytest.raises(csv_import.SheetError, match="no rows"):
        csv_import.read_sheet(b"\n\n,,\n")


# --------------------------------------------------------------------- guessing


def test_an_exact_header_is_not_taken_by_a_looser_match_on_an_earlier_field():
    assert csv_import.guess_mapping(["Company", "Role", "Applied through", "Date applied"]) == [
        "company",
        "role",
        "channel",
        "applied_at",
    ]
    for header in ("Deadline date", "Interview date"):
        mapping = csv_import.guess_mapping(["Company", "Role", header, "Date applied"])
        assert mapping[3] == "applied_at"
        assert mapping[2] != "applied_at"
    assert csv_import.guess_mapping(["Company", "Role", "Deadline date", "Date applied"])[2:] == [
        "deadline",
        "applied_at",
    ]


def test_headers_are_guessed_in_three_languages_and_never_twice():
    assert csv_import.guess_mapping(
        ["Company", "Role", "URL", "Date applied", "Status", "Salary", "Tags", "Notes"]
    ) == [
        "company",
        "role",
        "url",
        "applied_at",
        "status",
        "salary",
        "tags",
        "notes",
    ]
    assert csv_import.guess_mapping(
        ["Entreprise", "Poste", "Date de candidature", "Statut", "Lieu"]
    ) == [
        "company",
        "role",
        "applied_at",
        "status",
        "location",
    ]
    assert csv_import.guess_mapping(
        ["Empresa", "Cargo", "Data", "Estado", "Notas", "Observações"]
    ) == [
        "company",
        "role",
        "applied_at",
        "status",
        "notes",
        "notes",
    ]
    assert csv_import.guess_mapping(["Company", "Employer", "Whatever"]) == [
        "company",
        "ignore",
        "ignore",
    ]
    assert csv_import.clean_mapping(["role", "role", "bogus"], ["a", "b", "c"]) == [
        "role",
        "ignore",
        "ignore",
    ]


def test_dates_money_statuses_and_channels_are_read_as_people_write_them():
    assert csv_import.parse_date("2026-09-01") == dt.date(2026, 9, 1)
    assert csv_import.parse_date("03/09/2026") == dt.date(2026, 9, 3)
    assert csv_import.parse_date("03/09/2026", day_first=False) == dt.date(2026, 3, 9)
    assert csv_import.parse_date("13/09/26", day_first=False) == dt.date(2026, 9, 13), (
        "13 cannot be a month"
    )
    assert csv_import.parse_date("12 May 2026") == dt.date(2026, 5, 12)
    assert csv_import.parse_date("5 sept. 2026") == dt.date(2026, 9, 5)
    assert csv_import.parse_date("1 de setembro de 2026") == dt.date(2026, 9, 1)
    assert csv_import.parse_date("soon") is None and csv_import.parse_date("") is None

    assert csv_import.parse_money("55,000") == Decimal(55000)
    assert csv_import.parse_money("55 000 €") == Decimal(55000)
    assert csv_import.parse_money("60k") == Decimal(60000)
    assert csv_import.parse_money("1.234,50") == Decimal("1234.50")
    assert csv_import.parse_money("n/a") is None
    assert csv_import.parse_salary_range("55,000 - 65,000") == (Decimal(55000), Decimal(65000))
    assert csv_import.parse_salary_range("70k") == (Decimal(70000), Decimal(70000))

    assert csv_import.map_status("Interviewing") == ("interviewing", True)
    assert csv_import.map_status("Entretien") == ("interviewing", True)
    assert csv_import.map_status("Sans réponse") == ("ghosted", True)
    assert csv_import.map_status("Rejeitado") == ("rejected", True)
    assert csv_import.map_status("Waiting for the stars") == ("applied", False)
    assert csv_import.map_channel("LinkedIn") == "job_board"
    assert csv_import.map_channel("via a friend") == "referral"
    assert csv_import.map_channel("carrier pigeon") == "other"


# --------------------------------------------------------------------- importing


def test_the_import_makes_applications_listings_and_companies_and_skips_what_it_should(user):
    sheet = csv_import.read_sheet(ENGLISH.encode("utf-8"), "history.csv")
    mapping = csv_import.guess_mapping(sheet.headers)
    report = csv_import.perform(user, sheet, mapping)

    assert report.rows == 5
    assert report.applications == 2 and report.listings == 1 and report.companies_created == 3
    assert len(report.skipped) == 2
    assert any("no company" in line for line in report.skipped)
    assert any("same address" in line for line in report.skipped)

    aperture = Application.objects.for_user(user).get(posting__company__name="Aperture Science")
    assert aperture.status == Status.INTERVIEWING
    assert aperture.applied_at.date() == dt.date(2026, 9, 1)
    assert aperture.posting.salary_min == Decimal(55000) and aperture.posting.salary_max == Decimal(
        65000
    )
    assert sorted(tag.name for tag in aperture.tags.all()) == ["dream job", "remote"]
    provenance = aperture.events.get(kind=EventKind.OTHER)
    assert provenance.summary == "Imported from history.csv" and "Spoke to Cave" in provenance.body
    assert provenance.actor == "Imported from history.csv"
    assert aperture.events.filter(to_status=Status.APPLIED).exists(), (
        "the timeline says it was applied to"
    )

    black_mesa = Application.objects.for_user(user).get(posting__company__name="Black Mesa")
    assert black_mesa.status == Status.REJECTED and black_mesa.applied_at.date() == dt.date(
        2026, 9, 3
    )
    assert black_mesa.posting.salary_min == Decimal(60000)

    initech = JobPosting.objects.for_user(user).get(company__name="Initech")
    assert initech.derived_state == ListingState.NEW and not initech.applications.exists()
    assert "Not applied yet" in initech.description
    assert Company.objects.for_user(user).count() == 3


def test_importing_the_same_file_twice_creates_nothing_new(user):
    sheet = csv_import.read_sheet(ENGLISH.encode("utf-8"), "history.csv")
    mapping = csv_import.guess_mapping(sheet.headers)
    csv_import.perform(user, sheet, mapping)
    again = csv_import.perform(user, sheet, mapping)
    assert again.applications == 0 and again.listings == 1, (
        "a listing has no date to match on, so it repeats"
    )
    assert Application.objects.for_user(user).count() == 2
    assert sum("already recorded" in line for line in again.skipped) == 3


def test_an_unknown_status_becomes_applied_with_the_original_in_a_note(user):
    data = b"Company,Role,Date applied,Status\nAperture,Engineer,2026-09-01,Waiting for the stars\n"
    sheet = csv_import.read_sheet(data, "odd.csv")
    csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    application = Application.objects.for_user(user).get()
    assert application.status == Status.APPLIED
    note = application.events.get(kind=EventKind.OTHER)
    assert "Waiting for the stars" in note.body


def test_statuses_and_channels_match_whole_words_and_outcomes_win():
    for text in ("Rejected after interview", "Interview - rejected", "Offer declined", "No offer"):
        assert csv_import.map_status(text) == ("rejected", True), text
    assert csv_import.map_status("No") == ("rejected", True)
    for text in ("Unknown", "Not sure", "Nothing yet", "announced", "latest", "Contest"):
        assert csv_import.map_status(text) == ("applied", False), text
    assert csv_import.map_channel("Referral via LinkedIn") == "referral"
    assert csv_import.map_channel("eventually") == "other"


def test_an_unrecognised_status_word_inside_a_longer_one_is_kept_in_a_note(user):
    data = b"Company,Role,Date applied,Status\nAperture,Engineer,2026-09-01,Nothing yet\n"
    sheet = csv_import.read_sheet(data, "odd.csv")
    csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    application = Application.objects.for_user(user).get()
    assert application.status == Status.APPLIED
    assert "Nothing yet" in application.events.get(kind=EventKind.OTHER).body


@pytest.mark.parametrize(
    "data",
    [
        b"Company,Role,URL\nAcme,Dev,https://acme.example/1\nInitech,Analyst,\n",
        b"Company,Role,Status\nAcme,Dev,\nInitech,Analyst,\n",
    ],
    ids=["no status column", "blank status cell"],
)
def test_rows_with_no_status_and_no_date_become_listings(user, data):
    sheet = csv_import.read_sheet(data, "jobs.csv")
    report = csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    assert report.listings == 2 and report.applications == 0
    assert not Application.objects.for_user(user).exists()
    assert JobPosting.objects.for_user(user).count() == 2


def test_with_no_status_the_date_decides(user):
    data = b"Company,Role,Date applied\nAcme,Dev,2026-09-01\nInitech,Analyst,\n"
    sheet = csv_import.read_sheet(data, "jobs.csv")
    report = csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    assert report.applications == 1 and report.listings == 1


def test_a_listing_keeps_its_deadline_and_its_tags(user):
    data = b"Company,Role,Status,Deadline,Tags\nAcme,Dev,Wishlist,2026-10-15,remote; dream\n"
    sheet = csv_import.read_sheet(data, "wish.csv")
    csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    listing = JobPosting.objects.for_user(user).get()
    assert listing.closes_at == dt.date(2026, 10, 15)
    assert "Tags in the spreadsheet: remote, dream" in listing.description


def test_a_deadline_that_cannot_be_read_is_kept_in_the_note(user):
    data = (
        b"Company,Role,Status,Deadline\n"
        b"Acme,Dev,Wishlist,end of October\n"
        b"Initech,Analyst,Applied,end of October\n"
    )
    sheet = csv_import.read_sheet(data, "wish.csv")
    csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    listing = JobPosting.objects.for_user(user).get(company__name="Acme")
    assert listing.closes_at is None
    assert "Deadline: end of October" in listing.description
    application = Application.objects.for_user(user).get()
    note = application.events.get(summary__startswith="Imported from")
    assert "Deadline: end of October" in note.body


def test_month_names_are_read_in_the_offered_languages():
    for text, expected in (
        ("15. Oktober 2026", dt.date(2026, 10, 15)),
        ("5 de enero de 2026", dt.date(2026, 1, 5)),
        ("5 gennaio 2026", dt.date(2026, 1, 5)),
        ("5 stycznia 2026", dt.date(2026, 1, 5)),
        ("2026. január 5.", dt.date(2026, 1, 5)),
        ("5-Jan-26", dt.date(2026, 1, 5)),
    ):
        assert csv_import.parse_date(text) == expected, text
    assert csv_import.parse_date("end of October") is None


def test_french_headers_dates_and_statuses_import_as_they_mean(user):
    sheet = csv_import.read_sheet(FRENCH, "candidatures.csv")
    report = csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    assert report.applications == 2 and not report.skipped
    sg = Application.objects.for_user(user).get(posting__company__name="Société Générale")
    assert sg.status == Status.INTERVIEWING and sg.applied_at.date() == dt.date(2026, 9, 12)
    assert sg.posting.location == "Paris"
    vandelay = Application.objects.for_user(user).get(posting__company__name="Vandelay")
    assert vandelay.status == Status.GHOSTED and vandelay.applied_at.date() == dt.date(2026, 9, 5)


# ----------------------------------------------------------------------- the page


def test_the_page_uploads_maps_previews_and_imports(client, user):
    client.force_login(user)
    page = client.get(reverse("core:import_csv")).content.decode()
    assert "data-csv-upload" in page and reverse("core:import_csv_template") in page

    response = client.post(
        reverse("core:import_csv"),
        {
            "file": SimpleUploadedFile(
                "history.csv", ENGLISH.encode("utf-8"), content_type="text/csv"
            )
        },
    )
    assert response.status_code == 302

    mapping_page = client.get(reverse("core:import_csv")).content.decode()
    assert "data-csv-map" in mapping_page and "history.csv" in mapping_page
    assert '<option value="company" selected>' in mapping_page
    assert "data-preview" in mapping_page and "Research Engineer" in mapping_page
    # The duplicate row counts here: duplicates are only found as they are imported.
    summary = mapping_page.split("data-summary", 1)[1].split("</dl>", 1)[0]
    for label, number in (("Applications", 3), ("Listings", 1), ("Rows skipped", 1)):
        assert re.search(rf"<dt>{label}</dt><dd[^>]*>{number}</dd>", summary), label

    # Correct a guess and re-preview: read the Notes column as a description instead.
    fields = {
        f"column_{i}": key
        for i, key in enumerate(
            csv_import.guess_mapping(csv_import.read_sheet(ENGLISH.encode(), "h").headers)
        )
    }
    fields["column_7"] = "description"
    fields["date_order"] = "day_first"
    response = client.post(reverse("core:import_csv"), {**fields, "action": "preview"})
    assert (
        response.status_code == 200
        and '<option value="description" selected>' in response.content.decode()
    )

    response = client.post(reverse("core:import_csv"), {**fields, "action": "import"})
    assert response.status_code == 302 and response.url.startswith("/working/")
    watched = client.get(response.url).content.decode()
    assert "Imported 3 rows from history.csv" in watched
    done = client.get(reverse("core:import_csv_done", args=[response.url.split("/")[2]]))
    assert done.status_code == 200
    done = done.content.decode()
    assert "data-report" in done and "Imported" in done
    assert Application.objects.for_user(user).count() == 2
    assert (
        Application.objects.for_user(user)
        .get(posting__company__name="Aperture Science")
        .posting.description
        == "Spoke to Cave first"
    )
    assert client.get(reverse("core:import_csv")).content.decode().count("data-csv-upload") == 1, (
        "the file is forgotten"
    )


def test_the_page_refuses_a_mapping_without_company_and_role(client, user):
    client.force_login(user)
    client.post(
        reverse("core:import_csv"),
        {"file": SimpleUploadedFile("h.csv", ENGLISH.encode("utf-8"), content_type="text/csv")},
    )
    fields = {f"column_{i}": "ignore" for i in range(8)}
    response = client.post(reverse("core:import_csv"), {**fields, "action": "import"}, follow=True)
    assert "Map a column to Company" in response.content.decode()
    assert Application.objects.for_user(user).count() == 0


def test_the_template_and_the_your_data_page(client, user):
    client.force_login(user)
    response = client.get(reverse("core:import_csv_template"))
    assert response["Content-Type"].startswith("text/csv")
    body = response.content.decode()
    assert body.splitlines()[0].startswith("Company,Role,URL")
    sheet = csv_import.read_sheet(response.content, "template.csv")
    assert csv_import.guess_mapping(sheet.headers)[:3] == ["company", "role", "url"]
    assert reverse("core:import_csv") in client.get(reverse("core:export")).content.decode()


def test_importing_needs_signing_in(client, db):
    assert client.get(reverse("core:import_csv")).status_code == 302


# --------------------------------------------------------------------- the command


def test_the_command_shows_maps_dry_runs_and_imports(user, tmp_path, capsys):
    path = tmp_path / "history.csv"
    path.write_bytes(FRENCH)
    call_command("import_csv", user.username, str(path), "--show")
    shown = json.loads(capsys.readouterr().out)
    assert shown["Entreprise"] == "company" and shown["Statut"] == "status"

    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({**shown, "Lieu": "ignore"}), encoding="utf-8")
    call_command("import_csv", user.username, str(path), "--mapping", str(mapping), "--dry-run")
    out = capsys.readouterr().out
    assert "nothing imported" in out and Application.objects.for_user(user).count() == 0

    call_command("import_csv", user.email, str(path), "--mapping", str(mapping))
    assert "2 applications" in capsys.readouterr().out
    assert Application.objects.for_user(user).count() == 2
    assert Application.objects.for_user(user).filter(posting__location="").count() == 2, (
        "Lieu was ignored"
    )


def test_a_very_long_file_name_does_not_overflow_the_timeline_columns(user):
    name = "x" * 246 + ".csv"
    data = b"Company,Role,Date applied,Status\nAperture,Engineer,2026-09-01,Applied\n"
    sheet = csv_import.read_sheet(data, name)
    csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    events = list(ApplicationEvent.objects.filter(application__owner=user))
    assert events
    assert all(len(event.actor) <= 120 for event in events)
    assert all(len(event.summary) <= 250 for event in events)
    assert any(event.actor.endswith(".csv") for event in events), "the extension survives"


def _upload(client, name, text):
    client.post(
        reverse("core:import_csv"),
        {"file": SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")},
    )
    return client.get(reverse("core:import_csv")).content.decode()


def test_the_preview_names_the_status_not_its_key(client, user):
    from django.utils import translation

    client.force_login(user)
    data = "Company,Role,Date applied,Status\nAperture,Engineer,2026-09-01,Interviewing\n"
    page = _upload(client, "one.csv", data)
    preview = page.split("data-preview", 1)[1]
    assert str(Status.INTERVIEWING.label) in preview
    assert "interviewing" not in preview.split("</table>", 1)[0]
    with translation.override("fr-FR"):
        row = csv_import.parse_rows(
            csv_import.read_sheet(data.encode(), "one.csv"),
            csv_import.guess_mapping(csv_import.read_sheet(data.encode(), "one.csv").headers),
        )[0]
        assert row.status == "interviewing"
        assert row.status_label == str(Status.INTERVIEWING.label)
        assert row.status_label != row.status


def test_skip_reasons_pass_through_the_catalogue(user, monkeypatch):
    data = b"Company,Role,Date applied\nAperture,Engineer,2026-09-01\n"
    sheet = csv_import.read_sheet(data, "a.csv")
    mapping = csv_import.guess_mapping(sheet.headers)
    csv_import.perform(user, sheet, mapping)
    calls = []

    def fake(message):
        calls.append(message)
        return "[fr] " + message

    monkeypatch.setattr(csv_import, "gettext", fake)
    again = csv_import.perform(user, sheet, mapping)
    assert again.skipped and all(line.startswith("[fr] ") for line in again.skipped)
    assert "Row %(number)s: already recorded (%(role)s at %(company)s, %(date)s)" in calls
    assert "2026-09-01" not in again.skipped[0], "the date follows the locale's format"


def test_one_data_row_is_one_row_and_the_summary_pairs_labels_with_numbers(client, user):
    client.force_login(user)
    page = _upload(client, "one.csv", "Company,Role\nAperture,Engineer\n")
    assert "1 row." in page and "1 rows" not in page
    for label in ("Applications", "Listings", "Rows skipped"):
        assert re.search(rf"<dt>{label}</dt><dd[^>]*>\d+</dd>", page)


def test_the_section_title_is_translated_when_shown_not_when_imported():
    from django.utils.functional import Promise

    from postulo.core import views_import

    assert isinstance(views_import.SECTION["section_title"], Promise)


def test_tags_that_shared_a_slug_import_and_a_long_one_is_one_tag(user):
    from postulo.core.models import Tag

    long_name = "x" * 70
    sheet = "\n".join(
        [
            "Company,Role,Status,Tags",
            'Aperture,Engineer,Applied,"C++; C#"',
            'Black Mesa,Physicist,Applied,"удалённо; мечта"',
            f"Initech,Consultant,Applied,{long_name}",
            f"Vandelay,Importer,Applied,{long_name}",
        ]
    )
    parsed = csv_import.read_sheet(sheet.encode("utf-8"), "tags.csv")

    report = csv_import.perform(user, parsed, csv_import.guess_mapping(parsed.headers))

    assert report.applications == 4
    names = sorted(Tag.objects.filter(owner=user).values_list("name", flat=True))
    assert names == sorted(["C++", "C#", "удалённо", "мечта", "x" * 60])


# ------------------------------------------------------------------ at the cap (#555)


def _sheet_of(rows: int, *, companies: int = 50) -> csv_import.Sheet:
    lines = ["Company,Role,URL,Date applied,Status"]
    for number in range(rows):
        lines.append(
            f"Company {number % companies},Role {number},https://jobs.example/{number},"
            f"2026-0{1 + number % 9}-{1 + number % 28:02d},Applied"
        )
    return csv_import.read_sheet("\n".join(lines).encode(), "big.csv")


def test_a_sheet_at_the_cap_is_imported_in_queries_that_do_not_grow_with_the_rows(
    user, django_assert_max_num_queries
):
    from collections import deque

    from django.db import connection

    sheet = _sheet_of(csv_import.MAX_ROWS)
    mapping = csv_import.guess_mapping(sheet.headers)
    # Django keeps the last 9,000 queries and warns when it drops one; the whole run is wanted.
    connection.queries_log = deque(maxlen=csv_import.MAX_ROWS * 40)
    # A fixed handful for what is read once, three per distinct company, two per chunk, and
    # what writing a row costs (its inserts and savepoints). The lookups that used to come
    # with each row, ten or so apiece, are not in it.
    bound = (
        100
        + 50 * 3
        + (csv_import.MAX_ROWS // csv_import.CHUNK_ROWS) * 2
        + csv_import.MAX_ROWS * PER_ROW_WRITES
    )
    with django_assert_max_num_queries(bound):
        report = csv_import.perform(user, sheet, mapping)
    assert report.applications == csv_import.MAX_ROWS and not report.skipped
    assert Company.objects.for_user(user).count() == 50


#: What writing one application with its timeline costs, measured at 16 with savepoints, and
#: one more for the career mark a posting or application clears on its company (#683).
PER_ROW_WRITES = 19


def test_a_second_run_of_the_same_sheet_creates_nothing_and_asks_once(
    user, django_assert_max_num_queries
):
    sheet = _sheet_of(600)
    mapping = csv_import.guess_mapping(sheet.headers)
    csv_import.perform(user, sheet, mapping)
    with django_assert_max_num_queries(60):
        report = csv_import.perform(user, sheet, mapping)
    assert report.applications == 0 and len(report.skipped) == 600


def test_imported_history_is_not_announced(user, monkeypatch):
    from postulo.notifications import slow

    asked = []
    monkeypatch.setattr(slow, "anybody_wants", lambda *args: asked.append(args) or True)
    sheet = _sheet_of(5)
    csv_import.perform(user, sheet, csv_import.guess_mapping(sheet.headers))
    assert asked == []


def test_a_chunk_is_committed_on_its_own(user, monkeypatch):
    monkeypatch.setattr(csv_import, "CHUNK_ROWS", 2)
    sheet = _sheet_of(5)
    mapping = csv_import.guess_mapping(sheet.headers)
    calls = {"n": 0}
    original = csv_import.transaction.atomic

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(csv_import.transaction, "atomic", counting)
    csv_import.perform(user, sheet, mapping)
    assert calls["n"] >= 3


def test_somebody_elses_import_report_is_not_found(client, user, other_user):
    from postulo.core import errands

    errand = errands.send(
        "csv_import",
        user,
        filename="h.csv",
        data=__import__("base64").b64encode(ENGLISH.encode()).decode(),
        mapping=csv_import.guess_mapping(csv_import.read_sheet(ENGLISH.encode(), "h").headers),
        day_first=True,
        currency="EUR",
    )
    assert errand.state == "done" and errand.payload == {}
    client.force_login(other_user)
    assert client.get(reverse("core:import_csv_done", args=[errand.pk])).status_code == 404
    client.force_login(user)
    assert client.get(reverse("core:import_csv_done", args=[errand.pk])).status_code == 200
