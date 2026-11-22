"""Importers as a kind of plugin, and Europass as the one that ships.

Reading a career out of a file used to be a view calling `europass.read`. It is a plugin
now, which changes two things worth testing. The import page asks the registry what it can
read instead of naming Europass, so the next format is a plugin rather than a patch to a
view. And the refusals that keep a hostile file away from a parser belong to the *kind*
rather than to Europass, because "every plugin author remembers" is not a control.
"""

from __future__ import annotations

import codecs
import datetime as dt
from pathlib import Path

import pytest
from django.urls import reverse

from postulo import __version__
from postulo.plugins import base, registry
from postulo.plugins.europass import EuropassImporter

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------- the kind


def test_the_registry_knows_about_importers():
    assert "importer" in registry.GROUPS
    assert registry.GROUPS["importer"] == base.IMPORTER_GROUP


def test_an_importer_is_checked_against_its_own_protocol():
    """Not `ConnectedPlugin`, which is what every non-source kind used to get.

    An importer needs no connection and would have failed that check, so this is what
    lets one load at all.
    """
    assert registry._protocol_for("importer") is base.ImporterPlugin
    assert isinstance(EuropassImporter(), base.ImporterPlugin)


def test_europass_ships_in_the_box():
    found = registry.plugins("importer")
    assert [plugin.name for plugin in found] == ["europass", "json-resume", "linkedin-archive"]
    assert isinstance(found[0], EuropassImporter)


def test_it_says_what_it_is():
    plugin = EuropassImporter()
    assert plugin.kind == "importer"
    assert plugin.label == "Europass import"
    assert str(plugin.description)
    # Postulo's own, because that is the truth: a built-in ships with the application and
    # changes when it does. The two built-in connected plugins already do the same.
    assert plugin.version == __version__


# --------------------------------------------------------------- the refusals


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"", "empty"),
        (b"<a/>" + b" " * base.MAX_IMPORT_BYTES, "larger than"),
        (b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><a>&e;</a>', "document type"),
    ],
    ids=["empty", "oversized", "doctype"],
)
def test_the_kind_refuses_before_any_importer_sees_a_byte(data: bytes, expected: str):
    with pytest.raises(base.ImportRefused, match=expected):
        base.refuse_unreadable(data)


BOMB = '<!DOCTYPE x [<!ENTITY e "boom">]><SkillsPassport><a>&e;</a></SkillsPassport>'
PADDING = "<!--" + " " * 5000 + "-->"


@pytest.mark.parametrize(
    "data",
    [
        (PADDING + BOMB).encode(),
        BOMB.encode("utf-16-le"),
        BOMB.encode("utf-16-be"),
        BOMB.encode("utf-16"),
        (PADDING + BOMB).encode("utf-16-le"),
        (PADDING + BOMB).encode("utf-16-be"),
        (PADDING + BOMB).encode("utf-16"),
        codecs.BOM_UTF8 + (PADDING + BOMB).encode(),
    ],
    ids=[
        "after-a-long-comment",
        "utf16-le",
        "utf16-be",
        "utf16-bom",
        "utf16-le-padded",
        "utf16-be-padded",
        "utf16-bom-padded",
        "utf8-bom-padded",
    ],
)
def test_a_doctype_is_found_wherever_it_sits_and_in_whatever_encoding(data: bytes):
    """#378: it was looked for in the first 4 KB, as ASCII."""
    with pytest.raises(base.ImportRefused, match="document type"):
        base.refuse_unreadable(data)


def test_xml_without_a_doctype_is_not_refused_whatever_its_encoding():
    document = "<!--" + " " * 5000 + "--><SkillsPassport><a>x</a></SkillsPassport>"
    for data in (document.encode(), document.encode("utf-16-le"), document.encode("utf-16")):
        base.refuse_unreadable(data)


def test_a_doctype_in_json_is_not_an_attack():
    """The XML refusal must not become a JSON bug.

    `<!DOCTYPE` inside a string value of a perfectly ordinary JSON file is somebody's note
    about an HTML page, not entity expansion, and refusing it would be a defect rather
    than a defence.
    """
    base.refuse_unreadable(b'{"LearnerInfo": {"note": "<!DOCTYPE html> was in the advert"}}')


def test_europass_errors_are_the_kind_s_errors():
    """So a view catches one exception rather than knowing every importer's own."""
    from postulo.plugins.europass import reader as europass

    assert issubclass(europass.EuropassError, base.ImportRefused)


# ------------------------------------------------------ recognising a file


@pytest.mark.parametrize(
    "data,handled",
    [
        (b'{"SkillsPassport": {"LearnerInfo": {}}}', True),
        (b'{"LearnerInfo": {}}', True),
        (b"<SkillsPassport><LearnerInfo/></SkillsPassport>", True),
        # Truncated, and still plainly a Europass export. Saying "not readable XML" is far
        # more use to whoever exported it than "nothing here reads that".
        (b"<SkillsPassport><Learner", True),
        (b"<html><body>Hello</body></html>", False),
        (b'{"basics": {"name": "Alex"}}', False),
        # Any PDF: the one kind Postulo reads is Europass's, and Europass can say why it
        # could not read one where "nothing here reads that" says nothing useful (#244).
        (b"%PDF-1.7", True),
        (b"PK\x03\x04 a word processor's file", False),
        (b"", False),
    ],
)
def test_what_europass_claims(data: bytes, handled: bool):
    assert EuropassImporter().can_handle(data, "cv.txt") is handled


def test_a_renamed_export_is_still_an_export():
    """On the shape of the file, not its name. A Europass export arrives called all sorts
    of things, and somebody who renamed it has not thereby changed what it is."""
    data = b'{"LearnerInfo": {}}'
    assert EuropassImporter().can_handle(data, "whatever.dat")
    assert EuropassImporter().can_handle(data, "")


# ------------------------------------------------------------------ the page


def test_the_page_asks_the_registry_rather_than_naming_europass(client, user, monkeypatch):
    """The point of the whole change: a second importer needs no change to this view.

    A fake one is registered here, claims the file, and its record is what the review page
    shows — which could not happen if the view still called `europass.read` itself.
    """
    from postulo.plugins.europass import reader as europass

    class OnlyMine:
        name = "only-mine"
        version = "1.0"
        kind = "importer"
        label = "Only Mine"
        description = "A test double."

        def can_handle(self, data: bytes, filename: str = "") -> bool:
            return data.startswith(b"MINE")

        def read(self, data: bytes) -> europass.Record:
            return europass.Record(
                source="mine",
                education=[
                    {
                        "qualification": "Read by the double",
                        "institution": "Nowhere in particular",
                        "location": "",
                        "start_date": None,
                        "end_date": None,
                        "grade": "",
                        "highlights": "",
                    }
                ],
            )

    registry.register_builtin("importer", OnlyMine)
    monkeypatch.setattr(registry, "_cache", {})
    try:
        client.force_login(user)
        url = reverse("resume:europass_import")
        client.post(url, {"file": _upload("anything.dat", b"MINE and nothing else")})

        assert b"Read by the double" in client.get(url).content
    finally:
        registry.unregister_builtin("importer", OnlyMine)
        registry._cache.clear()


def test_a_file_nothing_can_read_says_so(client, user):
    client.force_login(user)
    url = reverse("resume:europass_import")

    response = client.post(url, {"file": _upload("cv.docx", WORD_FILE)}, follow=True)

    assert b"Nothing installed here reads that file" in response.content
    assert response.context["found"] is None


def test_a_pdf_is_for_europass_to_explain(client, user):
    """Not "nothing reads that": the reason, and what to upload instead (#244)."""
    client.force_login(user)
    url = reverse("resume:europass_import")

    response = client.post(url, {"file": _upload("holiday.pdf", b"%PDF-1.7 not a CV")}, follow=True)

    assert b"Nothing installed here reads that file" not in response.content
    assert b"nothing attached" in response.content
    assert response.context["found"] is None


def test_the_kind_refuses_an_upload_before_any_importer_is_asked(client, user):
    """A hostile file is refused whoever would have claimed it."""
    client.force_login(user)
    url = reverse("resume:europass_import")
    bomb = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><SkillsPassport>&e;</SkillsPassport>'

    response = client.post(url, {"file": _upload("cv.xml", bomb)}, follow=True)

    assert b"document type declaration" in response.content
    assert response.context["found"] is None


def _upload(name: str, data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


#: What a CV made in a word processor starts with: a zip. Nothing installed reads one.
WORD_FILE = b"PK\x03\x04 a CV made in a word processor"


# ------------------------------------------------------------- third parties (#105)


class _Installed:
    """What a package registered under `postulo.importers` looks like once loaded."""

    name = "hr-xml"
    version = "2.0"
    kind = "importer"
    label = "HR-XML"
    description = "A test double for somebody else's format."

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        return data.startswith(b"HRXML")

    def read(self, data: bytes):
        from postulo.plugins.api import Record

        return Record(source="hr-xml", projects=[{"name": "Read by the third party"}])


@pytest.fixture
def installed(monkeypatch):
    """`_Installed`, as the registry would find it through its entry point."""
    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [_Installed()] if kind == "importer" else [],
    )
    monkeypatch.setattr(registry, "_cache", {})
    yield _Installed
    registry._cache.clear()


def test_the_group_is_advertised():
    """An empty group is how an internal kind is enforced; this one is not empty."""
    assert base.IMPORTER_GROUP == "postulo.importers"
    assert registry.GROUPS["importer"] == "postulo.importers"


def test_a_third_party_importer_is_loaded_and_asked_first(installed):
    found = registry.plugins("importer")
    assert [plugin.name for plugin in found] == [
        "hr-xml",
        "europass",
        "json-resume",
        "linkedin-archive",
    ]
    assert isinstance(found[0], base.ImporterPlugin)


def test_a_package_that_is_not_an_importer_is_left_out(monkeypatch, caplog):
    """The `isinstance` check has a protocol to check against, and it is this one."""

    class NotOne:
        name = "not-one"
        kind = "importer"

        def read(self, data):  # `can_handle` is missing
            return None

    class Entry:
        name = "not-one"
        dist = None

        def load(self):
            return NotOne

    monkeypatch.setattr(
        registry, "entry_points", lambda group: [Entry()] if group == base.IMPORTER_GROUP else []
    )
    monkeypatch.setattr(registry, "_cache", {})
    try:
        found = registry.plugins("importer")
    finally:
        registry._cache.clear()
    assert [plugin.name for plugin in found] == ["europass", "json-resume", "linkedin-archive"]
    assert "not-one" in caplog.text


def test_the_record_is_on_the_surface():
    """A third party fills the same record Europass does, so it has to be importable."""
    from postulo.plugins import api
    from postulo.resume.importing import Record

    assert api.Record is Record
    assert "Record" in api.__all__


def test_the_page_hands_the_file_to_the_third_party(client, user, installed):
    client.force_login(user)
    url = reverse("resume:europass_import")

    client.post(url, {"file": _upload("export.hrxml", b"HRXML <career/>")})

    assert b"Read by the third party" in client.get(url).content


def test_the_refusals_run_before_the_third_party_is_asked(client, user, installed, monkeypatch):
    asked = []
    monkeypatch.setattr(
        installed, "can_handle", lambda self, data, filename="": asked.append(data) or True
    )
    client.force_login(user)
    url = reverse("resume:europass_import")
    bomb = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><HRXML>&e;</HRXML>'

    response = client.post(url, {"file": _upload("cv.xml", bomb)}, follow=True)

    assert b"document type declaration" in response.content
    assert asked == [], "the kind refused it, and no importer was asked"


def test_the_refusal_names_what_is_installed(client, user, installed):
    client.force_login(user)
    url = reverse("resume:europass_import")

    response = client.post(url, {"file": _upload("cv.docx", WORD_FILE)}, follow=True)

    assert (
        b"What is installed reads: HR-XML, Europass import, JSON Resume import, "
        b"LinkedIn data archive import." in response.content
    )


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("+4407911123456", "+447911123456"),
        ("+330612345678", "+33612345678"),
        ("+390669821234", "+390669821234"),
    ],
)
def test_any_importers_number_is_read_against_its_plan_on_the_way_in(user, written, stored):
    """The importer is the one place every record's number passes through, so a plugin
    that glues a dialling code to the national form is corrected here (#644)."""
    from postulo.plugins.api import Record
    from postulo.resume import importing

    importing.apply(user, Record(source="hr-xml", person={"phone": written}))

    assert user.profile.phone_numbers.get().number == stored


# ------------------------------------------------- a faulty importer (#611)


def _europass_file() -> bytes:
    return b'{"SkillsPassport": {"LearnerInfo": {"Identification": {}}}}'


def test_an_importer_that_raises_in_can_handle_is_skipped_and_logged(
    client, user, installed, monkeypatch, caplog
):
    def boom(self, data, filename=""):
        raise ValueError("not what I expected")

    monkeypatch.setattr(_Installed, "can_handle", boom)
    client.force_login(user)

    with caplog.at_level("ERROR"):
        response = client.post(
            reverse("resume:europass_import"),
            {"file": _upload("cv.json", _europass_file())},
            follow=True,
        )

    assert response.status_code == 200
    assert "Nothing installed here reads that file" not in response.content.decode()
    assert any("hr-xml" in r.getMessage() for r in caplog.records)


def test_an_importer_that_raises_in_read_says_so_instead_of_a_500(
    client, user, installed, monkeypatch, caplog
):
    def boom(self, data):
        raise RuntimeError("broken")

    monkeypatch.setattr(_Installed, "read", boom)
    client.force_login(user)

    with caplog.at_level("ERROR"):
        response = client.post(
            reverse("resume:europass_import"), {"file": _upload("a.dat", b"HRXML x")}, follow=True
        )

    assert response.status_code == 200
    assert b"HR-XML failed while reading that file" in response.content
    assert response.context["found"] is None
    assert any("hr-xml" in r.getMessage() for r in caplog.records)


def test_a_website_and_an_orcid_no_form_would_accept_are_reported_not_stored(user):
    """`apply` writes without a form, so it reads each value as its page does; what the
    page would refuse is named in the report and never printed on the CV (#610)."""
    from postulo.plugins.api import Record
    from postulo.resume import importing

    report = importing.apply(
        user,
        Record(
            source="hr-xml",
            person={"website": "javascript:alert(1)", "orcid": "not-an-orcid"},
        ),
    )

    assert not user.profile.web_links.exists()
    assert not user.profile.identifiers.exists()
    assert "website" not in report.profile_filled
    assert "orcid" not in report.profile_filled
    assert len(report.skipped) == 2
    assert any("javascript:alert(1)" in line for line in report.skipped)
    assert any("not-an-orcid" in line for line in report.skipped)


def test_a_good_website_and_orcid_are_still_stored(user):
    from postulo.plugins.api import Record
    from postulo.resume import importing

    report = importing.apply(
        user,
        Record(
            source="hr-xml",
            person={"website": "example.com/me", "orcid": "0000-0002-1825-0097"},
        ),
    )

    assert report.skipped == []
    assert user.profile.web_links.get().url == "https://example.com/me"
    assert user.profile.identifiers.get().value == "0000-0002-1825-0097"


# ------------------------------------------- the page names no format (#1260)


DATA = Path(__file__).parent / "data"


def _switch_off(name: str):
    from postulo.plugins.models import PluginPolicy

    return PluginPolicy.objects.create(
        plugin=name, person=None, state=PluginPolicy.State.FORCED_OFF
    )


class _Wider:
    """An importer that fills what a record has carried since #1260, and says what it accepts."""

    name = "wider"
    version = "1.0"
    kind = "importer"
    label = "Wider"
    description = "Reads a test file that starts with WIDER."
    accepts = (".wider", "application/x-wider")

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        return data.startswith(b"WIDER")

    def read(self, data: bytes):
        from postulo.plugins.api import ImportRefused, Record

        if data != b"WIDER":
            raise ImportRefused("That is not a whole WIDER file.")
        return Record(
            source="wider",
            source_label="Read as a WIDER file.",
            notes=["Volunteering was read as memberships."],
            skipped=["An interest in chess has nowhere to go."],
            experience=[
                {
                    "role": "Route planner",
                    "organisation": "Example Transportes, Lda.",
                    "location": "Lisboa",
                    "start_date": dt.date(2021, 3, 1),
                    "end_date": None,
                    "summary": "Planned daily routes.",
                    "highlights": "Cut empty kilometres by a tenth",
                }
            ],
            education=[
                {
                    "qualification": "Licenciatura",
                    "institution": "Example Polytechnic",
                    "location": "",
                    "start_date": dt.date(2014, 1, 1),
                    "end_date": dt.date(2017, 1, 1),
                    "grade": "15/20",
                    "highlights": "",
                    "field_of_study": "Logistics",
                }
            ],
            languages=[
                # No `levels`, which only Europass has; and a level in the file's own words.
                {
                    "name": "English",
                    "proficiency": "",
                    "stated": "Professional working proficiency",
                },
                {"name": "Portuguese", "proficiency": "native"},
            ],
            projects=[
                {
                    "name": "Depot move",
                    "summary": "Moved the depot.",
                    "role": "Lead",
                    "url": "https://example.org/depot",
                    "start_date": dt.date(2022, 1, 1),
                    "end_date": dt.date(2022, 6, 1),
                    "highlights": "On time",
                }
            ],
            certifications=[
                {
                    "name": "ADR driver training",
                    "issuer": "Example Institute",
                    "issued_on": dt.date(2022, 5, 10),
                    "expires_on": dt.date(2027, 5, 10),
                    "credential_url": "https://example.org/adr",
                },
                {"name": ""},
            ],
            honours=[
                {
                    "title": "Planner of the year",
                    "awarded_by": "Example Logistics SA",
                    "awarded_on": dt.date(2020, 12, 1),
                    "summary": "For the winter timetable.",
                    "url": "javascript:alert(1)",
                }
            ],
            courses=[
                {
                    "title": "Warehouse safety",
                    "provider": "Example Academy",
                    "start_date": dt.date(2019, 2, 1),
                    "end_date": dt.date(2019, 2, 5),
                    "hours": 40,
                    "summary": "A week.",
                    "url": "https://example.org/safety",
                },
                {"title": "Hours out of range", "hours": 0},
            ],
            publications=[
                {
                    "title": "Empty kilometres in regional haulage",
                    "publisher": "Example Press",
                    "date": "2023-05",
                    "url": "https://example.org/paper",
                    "doi": "https://doi.org/10.1000/182",
                },
                {"title": "A date nobody can read", "date": "last spring", "doi": "not-a-doi"},
            ],
        )


@pytest.fixture
def wider(monkeypatch):
    registry.register_builtin("importer", _Wider)
    monkeypatch.setattr(registry, "_cache", {})
    yield _Wider
    registry.unregister_builtin("importer", _Wider)
    registry._cache.clear()


def test_the_page_has_a_name_of_its_own_and_the_old_one_still_leads_to_it(client, user):
    """`resume:europass_import` is what the wiki told plugin authors to post to."""
    assert reverse("resume:import") == reverse("resume:europass_import")
    client.force_login(user)

    page = client.get(reverse("resume:europass_import"))

    assert page.status_code == 200
    assert page.resolver_match.view_name == "resume:import"


def test_the_page_names_no_format_and_says_what_is_installed(client, user, wider):
    client.force_login(user)

    body = client.get(reverse("resume:import")).content.decode()

    assert "Import a CV" in body
    assert "Import a Europass CV" not in body
    # Each importer in its own words: the label and the description it declares.
    assert "Europass import" in body and "europass.europa.eu" in body
    assert "Wider" in body and "Reads a test file that starts with WIDER." in body


def test_the_file_box_accepts_what_the_importers_say_they_read(client, user):
    client.force_login(user)

    body = client.get(reverse("resume:import")).content.decode()

    assert (
        'accept=".pdf,.xml,.json,application/pdf,text/xml,application/xml,application/json,'
        '.zip,.csv,application/zip,text/csv"' in body
    ), "what the box accepted while it named Europass, and what the other two add"


def test_a_second_importer_adds_what_it_reads_to_the_file_box(client, user, wider):
    client.force_login(user)

    body = client.get(reverse("resume:import")).content.decode()

    assert ".wider,application/x-wider" in body and ".pdf" in body


def test_an_importer_that_does_not_say_leaves_the_file_box_open(client, user, installed):
    """`accepts` is optional: a list that left a format out would grey its files out."""
    client.force_login(user)

    body = client.get(reverse("resume:import")).content.decode()

    assert 'type="file"' in body and "accept=" not in body


def test_what_an_importer_puts_in_accepts_is_held_to_extensions_and_media_types(wider):
    from postulo.resume.views import accepted_by

    class Odd(_Wider):
        accepts = (".ok", '" onfocus="alert(1)', "text/plain", 7, ".ok")

    assert accepted_by([Odd()]) == ".ok,text/plain"
    assert accepted_by([]) == ""


def test_with_no_importer_switched_on_there_is_no_file_box(client, user):
    client.force_login(user)
    for name in ("europass", "json-resume", "linkedin-archive"):
        _switch_off(name)

    page = client.get(reverse("resume:import"))
    body = page.content.decode()

    assert page.status_code == 200
    assert 'type="file"' not in body and "data-no-importer" in body
    # And a file sent anyway is told why nothing read it, in a sentence that names nothing.
    response = client.post(
        reverse("resume:import"), {"file": _upload("cv.json", _europass_file())}, follow=True
    )
    assert "Nothing that reads a CV from a file is switched on here" in response.content.decode()
    assert client.session.get("career_import") is None


def test_every_importer_that_ships_has_its_own_entry_behind_import(user):
    """The entry was governed by `europass` whatever read the file: with that one off and
    another on, *Import* was not offered at all."""
    from postulo.core import transfers

    entry = transfers.importer_entry(_Wider, 105)
    assert (entry.key, entry.module, entry.order) == ("career-wider-import", "wider", 105)
    assert entry.url(None) == reverse("resume:import")
    transfers.register(entry)
    try:
        offered = [t.key for t in transfers.offered("career", "import", user)]
        assert offered[0] == "career-europass-import" and "career-wider-import" in offered

        _switch_off("europass")

        offered = [t.key for t in transfers.offered("career", "import", user)]
        assert "career-europass-import" not in offered and "career-wider-import" in offered
    finally:
        transfers.forget(entry.key)


def test_the_sentence_about_the_format_is_the_importers_own(client, user, wider):
    client.force_login(user)
    url = reverse("resume:import")

    client.post(url, {"file": _upload("mine.wider", b"WIDER")})
    body = client.get(url).content.decode()

    assert "Read as a WIDER file." in body
    assert "Europass" not in body.split("data-import-found")[1]


def test_europass_says_which_of_its_formats_it_read():
    """The five sentences were the page's; they are the plugin's now, with its catalogue."""
    from postulo.plugins.europass import reader

    assert set(reader.SOURCE_LABELS) == {"candidate", "pdf-candidate", "xml", "pdf-xml", "json"}
    for name in ("europass.xml", "europass.json", "europass-candidate.xml"):
        record = EuropassImporter().read((DATA / name).read_bytes())
        assert record.source_label == str(reader.SOURCE_LABELS[record.source])
        assert record.source_label.startswith("Read as Europass")


def test_the_review_lists_the_four_new_sections_and_how_the_file_was_read(client, user, wider):
    client.force_login(user)
    url = reverse("resume:import")

    client.post(url, {"file": _upload("mine.wider", b"WIDER")})
    page = client.get(url)
    body = page.content.decode()

    for label in ("Certifications", "Honours and awards", "Courses", "Publications"):
        assert label in body
    for said in (
        "ADR driver training",
        "Example Institute",
        "Planner of the year",
        "Warehouse safety",
        "Empty kilometres in regional haulage",
    ):
        assert said in body
    # What was read and put somewhere unexpected is said apart from what was not read.
    assert "How it was read" in body and "Volunteering was read as memberships." in body
    assert "What could not be read" in body and "An interest in chess" in body
    # The file's own words for a level are shown, and no level is claimed for them.
    assert "Professional working proficiency" in body
    assert "not a level Postulo keeps, so none will be claimed" in body
    assert "Europass records five levels" not in body
    assert "Where the file states no level, none is written" in body
    counts = {str(row["label"]): row["total"] for row in page.context["found"]["counts"]}
    assert counts["Certifications"] == 2 and counts["Publications"] == 2


def test_the_five_levels_are_explained_only_for_a_file_that_has_them(client, user):
    client.force_login(user)
    url = reverse("resume:import")

    client.post(url, {"file": _upload("cv.xml", (DATA / "europass.xml").read_bytes())})
    body = client.get(url).content.decode()

    assert "Europass records five levels per language" in body


def test_confirming_writes_the_four_new_sections_and_the_optional_keys(client, user, wider):
    from postulo.resume.models import (
        Certification,
        Course,
        Education,
        Experience,
        Honour,
        Project,
        Publication,
    )

    client.force_login(user)
    url = reverse("resume:import")
    client.post(url, {"file": _upload("mine.wider", b"WIDER")})
    # Dates cross the session as text and come back as dates.
    held = client.session["career_import_data"]
    assert held["certifications"][0]["issued_on"] == "2022-05-10"
    assert held["projects"][0]["start_date"] == "2022-01-01"

    response = client.post(url, {"action": "confirm"}, follow=True)

    assert response.status_code == 200
    certification = Certification.objects.for_user(user).get()
    assert (certification.name, certification.issuer) == (
        "ADR driver training",
        "Example Institute",
    )
    assert certification.issued_on == dt.date(2022, 5, 10)
    assert certification.expires_on == dt.date(2027, 5, 10)
    assert certification.credential_url == "https://example.org/adr"
    honour = Honour.objects.for_user(user).get()
    assert (honour.title, honour.awarded_by) == ("Planner of the year", "Example Logistics SA")
    assert honour.awarded_on == dt.date(2020, 12, 1)
    assert honour.url == "", "only a web address is kept as a link"
    first, second = Course.objects.for_user(user).order_by("title")
    assert (first.title, first.hours) == ("Hours out of range", None)
    assert (second.title, second.provider, second.hours) == (
        "Warehouse safety",
        "Example Academy",
        40,
    )
    assert second.end_date == dt.date(2019, 2, 5)
    read, unread = Publication.objects.for_user(user).order_by("-date")
    assert (read.title, read.publisher) == (
        "Empty kilometres in regional haulage",
        "Example Press",
    )
    assert (read.date, read.doi, read.entry_type) == ("2023-05", "10.1000/182", "misc")
    assert read.cite_key and read.cite_key != unread.cite_key
    assert (unread.date, unread.doi) == ("", ""), "held to the rules the form holds a person to"
    # And the keys the models already held, which an import used to drop.
    assert Experience.objects.for_user(user).get().highlights == "Cut empty kilometres by a tenth"
    assert Education.objects.for_user(user).get().field_of_study == "Logistics"
    project = Project.objects.for_user(user).get()
    assert (project.role, project.url, project.highlights) == (
        "Lead",
        "https://example.org/depot",
        "On time",
    )
    assert (project.start_date, project.end_date) == (dt.date(2022, 1, 1), dt.date(2022, 6, 1))


def test_a_record_of_only_the_new_sections_is_not_empty():
    from postulo.plugins.api import Record

    assert Record().is_empty
    for name in ("certifications", "honours", "courses", "publications"):
        assert not Record(**{name: [{"title": "x", "name": "x"}]}).is_empty


def test_what_an_import_adds_is_only_ever_the_persons_own(user, other_user):
    """The writer never sees anybody but the owner it is handed (#129)."""
    from postulo.resume import importing
    from postulo.resume.models import Certification, Course, Honour, Publication

    importing.apply(user, _Wider().read(b"WIDER"))

    for model in (Certification, Honour, Course, Publication):
        assert model.objects.for_user(user).exists()
        assert not model.objects.for_user(other_user).exists()


def test_the_giver_is_linked_to_a_company_the_person_has_and_none_is_added(user):
    from postulo.jobs.models import Company
    from postulo.resume import importing
    from postulo.resume.models import Certification, Honour

    held = Company.objects.create(owner=user, name="Example Institute")

    importing.apply(user, _Wider().read(b"WIDER"))

    assert Certification.objects.for_user(user).get().company == held
    assert Honour.objects.for_user(user).get().company is None
    assert Company.objects.for_user(user).count() == 1, "a file must not fill the list (#683)"


# --------------------------------------------- the published contract check (#1260)


EUROPASS_SAMPLES = (
    "europass.xml",
    "europass.json",
    "europass-candidate.xml",
    "europass-candidate-sample.xml",
    "europass-cedefop-shape.xml",
)

#: Files Europass must leave to somebody else.
NOT_EUROPASS = (
    b'{"basics": {"name": "Alex"}}',
    b"<html><body>Hello</body></html>",
    WORD_FILE,
)


def _samples() -> list[bytes]:
    return [(DATA / name).read_bytes() for name in EUROPASS_SAMPLES]


def test_europass_meets_the_contract_every_importer_is_held_to():
    """No `django_db` needed by the check: it is this module's mark that grants one."""
    from postulo.plugins.testing import assert_is_an_importer

    assert_is_an_importer(EuropassImporter(), samples=_samples(), not_samples=NOT_EUROPASS)


def test_the_test_double_meets_it_too():
    from postulo.plugins.testing import assert_is_an_importer

    assert_is_an_importer(_Wider(), samples=[b"WIDER"], not_samples=[b"NARROW"])


def _broken(**changes):
    """`_Wider` with one thing wrong, which is how a real importer is wrong."""
    return type("Broken", (_Wider,), changes)()


def _reads(record_changes):
    def read(self, data: bytes):
        from postulo.plugins.api import ImportRefused

        if data != b"WIDER":
            raise ImportRefused("no")
        record = _Wider().read(data)
        record_changes(record)
        return record

    return read


def _writes(self, data: bytes):
    from postulo.jobs.models import Industry
    from postulo.plugins.api import ImportRefused

    if data != b"WIDER":
        raise ImportRefused("no")
    Industry.objects.filter(pk=0).update(name="written by an importer")
    return _Wider().read(data)


def _raises(self, data: bytes):
    if data != b"WIDER":
        raise ValueError("not what I expected")
    return _Wider().read(data)


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"kind": "source"}, "its kind is"),
        ({"description": ""}, "no description"),
        ({"accepts": ("wider",)}, "neither an extension nor a media type"),
        ({"accepts": [".wider"]}, "accepts is a tuple"),
        (
            {"can_handle": lambda self, data, filename="": data[0] == 87},
            "can_handle raised IndexError",
        ),
        (
            {"can_handle": lambda self, data, filename="": 1 if data == b"WIDER" else 0},
            "not a bool",
        ),
        ({"can_handle": lambda self, data, filename="": True}, "claims a file that is not its"),
        ({"can_handle": lambda self, data, filename="": False}, "does not recognise the sample"),
        ({"read": lambda self, data: {"experience": []}}, "not the Record on the plugin surface"),
        ({"read": _raises}, "read raised ValueError"),
        ({"read": _writes}, "wrote to the database"),
        (
            {"read": _reads(lambda record: record.experience[0].pop("summary"))},
            "experience[0] has no ['summary']",
        ),
        (
            {"read": _reads(lambda record: record.experience[0].update(organization="x"))},
            "keys nothing reads: ['organization']",
        ),
        (
            {
                "read": _reads(
                    lambda record: record.experience[0].update(
                        start_date=dt.datetime(2021, 3, 1, 9, 0)
                    )
                )
            },
            "not a datetime.date or None",
        ),
        (
            {"read": _reads(lambda record: record.experience[0].update(start_date="2021-03"))},
            "not a datetime.date or None",
        ),
        (
            {"read": _reads(lambda record: record.courses[0].update(hours="40"))},
            "courses[0]: hours is str",
        ),
        (
            {"read": _reads(lambda record: record.person.update(surname="Morgan"))},
            "person has keys nothing reads",
        ),
        (
            {"read": _reads(lambda record: record.skipped.append(7))},
            "skipped is not a list of sentences",
        ),
    ],
    ids=[
        "kind",
        "description",
        "accepts-bare-word",
        "accepts-list",
        "can-handle-raises",
        "can-handle-not-bool",
        "claims-everything",
        "claims-nothing",
        "not-a-record",
        "read-raises",
        "read-writes",
        "missing-key",
        "misspelt-key",
        "datetime",
        "date-as-text",
        "hours-as-text",
        "person-key",
        "skipped",
    ],
)
def test_the_contract_check_names_what_is_wrong(changes, said):
    from postulo.plugins.testing import assert_is_an_importer

    with pytest.raises(AssertionError) as raised:
        assert_is_an_importer(_broken(**changes), samples=[b"WIDER"], not_samples=[b"NARROW"])

    assert said in str(raised.value)


def test_the_contract_check_needs_a_sample():
    from postulo.plugins.testing import assert_is_an_importer

    with pytest.raises(AssertionError, match="at least one file"):
        assert_is_an_importer(_Wider(), samples=[])


def test_every_list_of_a_record_has_its_keys_written_down():
    """`ROWS` is the contract: a list added to `Record` without its keys fails here."""
    import dataclasses

    from postulo.resume.importing import ROWS, Record

    lists = {
        field.name
        for field in dataclasses.fields(Record)
        if field.name not in ("person", "source", "source_label", "locale", "skipped", "notes")
    }
    assert lists == set(ROWS)
    for name, rows in ROWS.items():
        assert set(rows.dates) <= set(rows.known), name
        assert not set(rows.required) & set(rows.optional), name
