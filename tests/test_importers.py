"""Importers as a kind of plugin, and Europass as the one that ships.

Reading a career out of a file used to be a view calling `europass.read`. It is a plugin
now, which changes two things worth testing. The import page asks the registry what it can
read instead of naming Europass, so the next format is a plugin rather than a patch to a
view. And the refusals that keep a hostile file away from a parser belong to the *kind*
rather than to Europass, because "every plugin author remembers" is not a control.
"""

from __future__ import annotations

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
    assert [plugin.name for plugin in found] == ["europass"]
    assert isinstance(found[0], EuropassImporter)


def test_it_says_what_it_is():
    plugin = EuropassImporter()
    assert plugin.kind == "importer"
    assert plugin.label == "Europass"
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
        (b"%PDF-1.7", False),
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

    response = client.post(url, {"file": _upload("holiday.pdf", b"%PDF-1.7 not a CV")}, follow=True)

    assert b"Nothing installed here reads that file" in response.content
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
    assert [plugin.name for plugin in found] == ["hr-xml", "europass"]
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
    assert [plugin.name for plugin in found] == ["europass"]
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

    response = client.post(url, {"file": _upload("holiday.pdf", b"%PDF-1.7 not a CV")}, follow=True)

    assert b"What is installed reads: HR-XML, Europass." in response.content
