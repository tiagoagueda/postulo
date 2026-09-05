"""Bringing a spreadsheet in: read anything Excel writes, guess the columns, import once."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from postulo.applications.models import Application, EventKind, Status
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


def test_empty_huge_and_rowless_files_are_refused():
    with pytest.raises(csv_import.SheetError, match="empty"):
        csv_import.read_sheet(b"   ")
    with pytest.raises(csv_import.SheetError, match="2 MB"):
        csv_import.read_sheet(b"x" * (csv_import.MAX_BYTES + 1))
    with pytest.raises(csv_import.SheetError, match="no rows"):
        csv_import.read_sheet(b"\n\n,,\n")


# --------------------------------------------------------------------- guessing


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
    assert "3 applications, 1 listings, 1 rows skipped" in mapping_page

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
    assert response.status_code == 200
    done = response.content.decode()
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
