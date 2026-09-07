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
from postulo.resume.importers import EuropassImporter

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
    from postulo.resume import europass

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
    from postulo.resume import europass

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
