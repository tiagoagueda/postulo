"""The career half of a LinkedIn data archive, read in (#1260).

The fixtures under `tests/data/linkedin-archive/` are CSV text files for one made-up person,
*Alex Morgan*, at made-up companies under `example.org`; this module builds the ZIP from
them, so no binary is committed and **no real person's export is ever used**. Their header
rows come from LinkedIn's help page and from open-source readers of the archive, not from a
real archive (decision D9): a renamed column is something the review names.
"""

from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
from pathlib import Path

import pytest
from django.urls import reverse

from postulo.plugins import base, registry
from postulo.plugins.linkedin_archive import LinkedInArchiveImporter, archive
from postulo.plugins.testing import assert_is_an_importer

pytestmark = pytest.mark.django_db

DATA = Path(__file__).parent / "data" / "linkedin-archive"

#: Every fixture, as LinkedIn names the files.
FILES = sorted(path.name for path in DATA.glob("*.csv"))


def build(
    files: dict[str, bytes] | None = None,
    *,
    folder: str = "Basic_LinkedInDataExport_11-17-2026/",
) -> bytes:
    """A ZIP of the fixtures (or of ``files``), in a top folder, as LinkedIn sends one."""
    found = files if files is not None else {name: (DATA / name).read_bytes() for name in FILES}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as made:
        for name, content in found.items():
            made.writestr(f"{folder}{name}", content)
    return buffer.getvalue()


def patch_entry(data: bytes, name: str, *, size: int | None = None, flags: int | None = None):
    """The ZIP with what its central directory says of one member changed: a lie, on purpose."""
    found = bytearray(data)
    at = 0
    while (at := found.find(b"PK\x01\x02", at)) != -1:
        name_length = struct.unpack_from("<H", found, at + 28)[0]
        stored = bytes(found[at + 46 : at + 46 + name_length]).decode()
        if stored.endswith(name):
            if size is not None:
                struct.pack_into("<I", found, at + 24, size)
            if flags is not None:
                struct.pack_into("<H", found, at + 8, flags)
            return bytes(found)
        at += 4
    raise AssertionError(f"{name} is not in the archive")


def read(data: bytes | None = None):
    return LinkedInArchiveImporter().read(data if data is not None else build())


# ------------------------------------------------------------------ the contract


def test_it_is_an_importer():
    word = build({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"}, folder="")
    postulo = build({"postulo.json": b"{}", "Profile.csv": b"First Name"}, folder="")
    assert_is_an_importer(
        LinkedInArchiveImporter(),
        samples=[build(), (DATA / "Positions.csv").read_bytes()],
        not_samples=[word, postulo, b"%PDF-1.7", b'{"basics": {}}', b"Name\r\nPython\r\n"],
    )


def test_it_ships_on_and_says_what_it_is():
    plugin = registry.find_any("linkedin-archive")
    assert isinstance(plugin, LinkedInArchiveImporter)
    assert str(base.label_of(plugin)) == "LinkedIn data archive import"
    assert ".zip" in plugin.accepts and ".csv" in plugin.accepts
    assert [p.name for p in registry.plugins("importer")] == [
        "europass",
        "json-resume",
        "linkedin-archive",
    ]


def test_a_word_file_and_a_postulo_archive_are_not_claimed_even_with_a_profile_in_them():
    for marker in ("[Content_Types].xml", "mimetype", "manifest.json", "postulo.json"):
        data = build({marker: b"x", "Profile.csv": (DATA / "Profile.csv").read_bytes()})
        assert not LinkedInArchiveImporter().can_handle(data), marker


def test_mixed_case_names_in_any_folder_are_found():
    files = {name.upper(): (DATA / name).read_bytes() for name in ("Positions.csv",)}
    data = build(files, folder="some/deeper/folder/")

    assert LinkedInArchiveImporter().can_handle(data)
    assert read(data).experience[0]["role"] == "Route planner"


# ------------------------------------------------------------------ the career


def test_the_career_files_read_into_postulos_terms():
    record = read()

    assert record.source == "linkedin-archive"
    assert record.source_label.startswith("Read as a LinkedIn data archive: 13 files were read")
    assert record.person == {
        "first_name": "Alex",
        "last_name": "Morgan",
        "headline": "Logistics planner",
        "location": "Lisbon, Portugal",
        "website": "https://alex-morgan.example.org",
        "phone": "+351 21 000 0000",
        "email": "alex.morgan@example.org",
    }
    first, second, third = record.experience
    # A description with a line break and a comma, and a position that is current.
    assert first["organisation"] == "Example Transportes, Lda."
    assert first["summary"] == (
        "Planned daily routes.\nCut empty kilometres by a tenth, in two years."
    )
    assert (first["start_date"], first["end_date"]) == (dt.date(2021, 3, 1), None)
    assert (second["start_date"], second["end_date"]) == (dt.date(2017, 9, 1), dt.date(2021, 2, 1))
    assert third["start_date"] is None, "no start date: the writer refuses it, in words"
    [school] = record.education
    assert (school["start_date"], school["end_date"]) == (dt.date(2014, 1, 1), dt.date(2017, 1, 1))
    assert school["highlights"] == "Final project on depot location.\nStudent union"
    assert record.skill_groups == [{"name": "Skills", "skills": ["Route optimisation", "SAP TM"]}]
    assert record.certifications[0]["issuer"] == "Example Institute"
    assert record.certifications[0]["expires_on"] == dt.date(2027, 5, 1)
    assert record.projects[0]["url"] == "https://example.org/depot"
    assert record.honours[0]["awarded_on"] == dt.date(2020, 12, 1)
    assert record.courses == [{"title": "Warehouse safety"}]
    assert record.publications[0]["date"] == "2023-05"
    assert record.memberships[0]["organisation"] == "Example Food Bank"


def test_only_native_is_a_level_and_the_other_four_phrases_are_shown():
    """Decision D6: LinkedIn's phrases are not CEFR levels."""
    record = read()

    assert [(row["name"], row["proficiency"]) for row in record.languages] == [
        ("Portuguese", "native"),
        ("English", ""),
        ("Spanish", ""),
        ("French", ""),
        ("German", ""),
    ]
    assert record.languages[1]["stated"] == "Full professional proficiency"
    assert any("not CEFR levels" in note for note in record.notes)


def test_volunteering_is_read_as_memberships_and_the_review_says_so():
    record = read()

    assert any("read as memberships" in note for note in record.notes)


def test_what_has_no_place_is_named():
    said = " ".join(read().skipped)

    assert "Profile.csv: these have no place" in said and "Summary" in said
    assert "License Number" in said and "Cause" in said and "Number" in said
    assert "Employment Type" in said, "a column LinkedIn added is named, not a failure (D9)"


def test_connections_and_messages_are_named_and_never_opened(monkeypatch):
    """Other people's data (decision D3): not opened, not even to count the rows."""
    data = build()
    opened = []
    real = zipfile.ZipFile.open

    def watching(self, name, *args, **kwargs):
        opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return real(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", watching)

    record = read(data)

    assert not any(name.lower().endswith(("connections.csv", "messages.csv")) for name in opened)
    said = " ".join(record.skipped)
    assert "Connections.csv holds other people's details, so it was not opened" in said
    assert "These files were in the archive and were not read: messages.csv" in said
    assert "Jordan" not in said and all("Jordan" not in r["name"] for r in record.references)


def test_the_job_files_are_named_and_not_read():
    data = build({**{n: (DATA / n).read_bytes() for n in FILES}, "Jobs/Saved Jobs.csv": b"x"})

    assert "Saved Jobs.csv" in " ".join(read(data).skipped)


def test_many_unopened_files_are_named_forty_at_a_time():
    files = {"Positions.csv": (DATA / "Positions.csv").read_bytes()}
    files.update({f"Ads_{n:03d}.csv": b"x" for n in range(45)})

    said = " ".join(read(build(files)).skipped)

    assert "Ads_039.csv" in said and "Ads_040.csv" not in said and "and 5 more" in said


# ------------------------------------------------------------- the archive's guards


def test_a_member_declaring_three_gigabytes_is_not_opened(monkeypatch):
    data = patch_entry(build(), "Positions.csv", size=3 * 1024**3)
    opened = []
    real = zipfile.ZipFile.open
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda self, name, *a, **k: (
            opened.append(getattr(name, "filename", name)) or real(self, name, *a, **k)
        ),
    )

    record = read(data)

    assert record.experience == []
    assert not any(name.endswith("Positions.csv") for name in opened)
    assert "Positions.csv is larger than 2 MB unpacked" in " ".join(record.skipped)


def test_a_member_that_understates_its_size_is_caught_by_the_bounded_read():
    big = (DATA / "Skills.csv").read_bytes() + b"Route optimisation\r\n" * 150_000
    data = patch_entry(
        build({"Positions.csv": b"Company Name,Title\r\n", "Skills.csv": big}),
        "Skills.csv",
        size=100,
    )

    record = read(data)

    assert record.skill_groups == []
    assert "Skills.csv" in " ".join(record.skipped)


def test_an_encrypted_member_is_named_and_left():
    data = patch_entry(build(), "Education.csv", flags=0x1)

    record = read(data)

    assert record.education == []
    assert "Education.csv is encrypted" in " ".join(record.skipped)


def test_a_nested_zip_is_named_and_not_opened():
    data = build({"Positions.csv": (DATA / "Positions.csv").read_bytes(), "Inner.zip": build()})

    record = read(data)

    assert "Inner.zip" in " ".join(record.skipped)
    assert record.person == {}


def test_nothing_is_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    read()

    assert list(tmp_path.iterdir()) == []


def test_a_renamed_column_names_the_file_rather_than_failing():
    renamed = b"Employer,Job Title,Started\r\nExample,Planner,Mar 2021\r\n"
    data = build({"Profile.csv": (DATA / "Profile.csv").read_bytes(), "Positions.csv": renamed})

    record = read(data)

    assert record.person["first_name"] == "Alex"
    assert "Positions.csv does not start with the columns Postulo knows" in " ".join(record.skipped)


def test_a_loose_csv_is_read_on_its_own():
    record = read((DATA / "Positions.csv").read_bytes())

    assert len(record.experience) == 3
    assert record.source_label.startswith("Read as a LinkedIn data archive: 1 file was read")


def test_an_archive_with_no_career_file_is_not_claimed():
    assert not LinkedInArchiveImporter().can_handle(build({"messages.csv": b"x"}))


def test_the_archive_reader_stops_at_its_entry_limit(monkeypatch):
    monkeypatch.setattr(archive, "MAX_ENTRIES", 3)

    record = read()

    assert any("more than 3 files" in note for note in record.skipped)


# ------------------------------------------------------------------ the page


def _upload(name: str, data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, data, content_type="application/zip")


def test_the_page_reads_it_and_writes_it_when_told(client, user):
    from postulo.resume.models import Experience, LanguageSkill, Membership

    client.force_login(user)
    url = reverse("resume:import")

    client.post(url, {"file": _upload("Basic_LinkedInDataExport.zip", build())})
    body = client.get(url).content.decode()

    assert "Read as a LinkedIn data archive" in body
    assert "Full professional proficiency" in body
    assert "not a level Postulo keeps, so none will be claimed" in body
    assert "Connections.csv holds other people" in body
    assert not Experience.objects.for_user(user).exists()

    response = client.post(url, {"action": "confirm"}, follow=True)

    # Two of three: the one with no start date is named, as the writer always has.
    assert Experience.objects.for_user(user).count() == 2
    assert "Intern: no start date" in response.content.decode()
    assert LanguageSkill.objects.for_user(user).get(name="Portuguese").proficiency == "native"
    assert LanguageSkill.objects.for_user(user).get(name="English").proficiency == ""
    assert Membership.objects.for_user(user).get().organisation == "Example Food Bank"
    from allauth.account.models import EmailAddress

    assert not EmailAddress.objects.filter(email="alex.morgan@example.org").exists()


def _refusal(client, name: str, data: bytes) -> str:
    response = client.post(reverse("resume:import"), {"file": _upload(name, data)}, follow=True)
    return " ".join(str(message) for message in response.context["messages"])


def test_an_archive_over_the_limit_is_refused_with_what_to_ask_linkedin_for(client, user):
    """Decision D4: the cap stays, and the refusal says how to get a smaller archive."""
    client.force_login(user)
    big = b"PK" + bytes(base.MAX_IMPORT_BYTES + 1)

    said = _refusal(client, "Complete_LinkedInDataExport.zip", big)

    assert "is over 5 MB" in said
    assert "Ask LinkedIn for a copy of only the categories you want" in said


def test_a_pdf_over_the_limit_is_not_told_about_linkedin(client, user):
    client.force_login(user)
    big = b"%PDF-1.7" + bytes(base.MAX_IMPORT_BYTES + 1)

    said = _refusal(client, "cv.pdf", big)

    assert "is over 5 MB" in said and "LinkedIn" not in said


def test_switched_off_it_reads_nothing(client, user):
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin="linkedin-archive", person=None, state=PluginPolicy.State.FORCED_OFF
    )
    client.force_login(user)

    response = client.post(
        reverse("resume:import"), {"file": _upload("a.zip", build())}, follow=True
    )

    assert "Nothing installed here reads that file" in response.content.decode()
    assert client.session.get("career_import") is None
