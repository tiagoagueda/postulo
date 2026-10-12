"""The module "Extend file formats" and its sub-modules (#877).

PDF is always on; Word, OpenDocument, iCalendar, vCard and Europass can be switched off, and
then they are not offered -- not in a menu, not as a button, not at their address, where a
signed-in person is told there is nothing there. Switching the module off switches them all.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.core import modules, transfers
from postulo.documents import docx, formats, odt, outline, readers
from postulo.documents.forms import UploadedDocumentForm
from postulo.plugins import base, policy, registry
from postulo.plugins.file_formats import FILE_FORMATS
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

PASSWORD = "not-a-real-password"

SUB_MODULES = {
    "format-pdf": ("PDF", "file-text"),
    "format-docx": ("DOCX", "file-type"),
    "format-odt": ("ODT", "file-type-2"),
    "format-icalendar": ("iCalendar", "calendar-days"),
    "format-vcard": ("vCard", "contact-round"),
    "format-geojson": ("GeoJSON", "map"),
    "europass": ("Europass", "id-card"),
}


def switch_off(name, person=None):
    return PluginPolicy.objects.create(
        plugin=name, person=person, state=PluginPolicy.State.FORCED_OFF
    )


def signed_in(client, user):
    client.force_login(user)
    return client


# --------------------------------------------------------------- the structure


def test_the_module_ships_and_holds_every_format():
    found = registry.find_plugin("feature", FILE_FORMATS)
    manifest = base.manifest_of(found)
    assert str(manifest.label) == "Extend file formats"
    assert manifest.icon == "files"
    assert set(policy.children_of(FILE_FORMATS)) == set(SUB_MODULES)


@pytest.mark.parametrize(("name", "wanted"), SUB_MODULES.items())
def test_each_format_is_a_sub_module_with_its_icon(name, wanted):
    label, icon = wanted
    manifest = base.manifest_of(registry.find_any(name))
    assert (manifest.parent, str(manifest.label), manifest.icon) == (FILE_FORMATS, label, icon)


def test_europass_keeps_its_name_and_is_still_an_importer():
    found = registry.find_plugin("importer", "europass")
    assert found is not None
    assert base.manifest_of(found).parent == FILE_FORMATS


def test_switching_the_module_off_switches_the_formats_off_but_not_pdf(user):
    switch_off(FILE_FORMATS)
    for name in SUB_MODULES:
        decision = policy.decide(name, user)
        assert decision.on == (name == "format-pdf"), name


# ---------------------------------------------------------------- PDF is required


def test_pdf_is_on_whatever_a_row_says(user):
    switch_off("format-pdf")
    decision = policy.decide("format-pdf", user)
    assert decision.on and not decision.theirs
    assert decision.decided_by == "required"


def test_pdf_cannot_be_switched_off_from_the_page(client, admin_user):
    signed_in(client, admin_user)
    body = client.get(reverse("server:plugins")).content.decode()
    assert 'name="row:format-pdf"' not in body
    client.post(reverse("server:plugin_policy"), {"row:format-pdf": "1"})
    assert not PluginPolicy.objects.filter(plugin="format-pdf").exists()


# ---------------------------------------------------------- transfers and menus


def keys(subject, direction, user):
    return {t.key for t in transfers.offered(subject, direction, user)}


def test_transfers_follow_their_modules(user):
    assert "calendar-ical-export" in keys(transfers.CALENDAR, transfers.EXPORT, user)
    assert "details-vcard-export" in keys(transfers.DETAILS, transfers.EXPORT, user)
    assert "career-europass-import" in keys(transfers.CAREER, transfers.IMPORT, user)
    for name in ("format-icalendar", "format-vcard", "europass"):
        switch_off(name)
    assert "calendar-ical-export" not in keys(transfers.CALENDAR, transfers.EXPORT, user)
    assert "calendar-ical-import" not in keys(transfers.CALENDAR, transfers.IMPORT, user)
    assert "details-vcard-export" not in keys(transfers.DETAILS, transfers.EXPORT, user)
    assert "career-europass-import" not in keys(transfers.CAREER, transfers.IMPORT, user)
    assert "career-record-import" in keys(transfers.CAREER, transfers.IMPORT, user)


def test_the_document_formats_follow_their_modules(user):
    assert {"txt", "odt", "docx"} <= {f.key for f in formats.offered_to(user)}
    switch_off("format-docx")
    assert "docx" not in {f.key for f in formats.offered_to(user)}
    assert formats.get_for(user, "docx") is None
    assert formats.get_for(user, "odt") is not None
    assert formats.get("docx") is not None, "the registry itself is not the policy"


def test_a_module_off_for_one_person_leaves_the_others(user, other_user):
    switch_off("format-vcard", person=user)
    assert not modules.is_on(user, "format-vcard")
    assert modules.is_on(other_user, "format-vcard")


def test_an_unknown_name_is_on():
    assert modules.is_on(None, "no-such-module")


# --------------------------------------------------------------------- the addresses


@pytest.mark.parametrize(
    ("module", "url"),
    [
        ("format-icalendar", "applications:calendar_download"),
        ("format-icalendar", "applications:ical_import"),
        ("format-icalendar", "applications:reminder_calendar"),
        ("format-icalendar", "applications:interview_calendar"),
        ("format-vcard", "jobs:own_vcard"),
        ("format-vcard", "jobs:contacts_vcard"),
        ("format-vcard", "jobs:contact_vcards"),
    ],
)
def test_an_address_answers_while_its_module_is_on_and_404_while_it_is_off(
    client, user, module, url
):
    signed_in(client, user)
    assert client.get(reverse(url)).status_code == 200
    switch_off(module)
    assert client.get(reverse(url)).status_code == 404
    PluginPolicy.objects.filter(plugin=module).delete()
    assert client.get(reverse(url)).status_code == 200, "and it comes back at the same address"


def test_a_stranger_is_still_sent_to_sign_in_not_told_a_module_is_off(client):
    switch_off("format-vcard")
    response = client.get(reverse("jobs:own_vcard"))
    assert response.status_code == 302


def test_a_companys_card_is_the_vcard_modules_and_its_json_is_not(client, user):
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=user, name="Aperture")
    signed_in(client, user)
    switch_off("format-vcard")
    url = reverse("jobs:company_export", args=[company.pk])
    assert client.get(url, {"format": "vcard"}).status_code == 404
    assert client.get(url, {"format": "json"}).status_code == 200


def test_the_company_page_offers_only_the_formats_that_are_on(client, user):
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=user, name="Aperture")
    signed_in(client, user)
    switch_off("format-vcard")
    body = client.get(company.get_absolute_url()).content.decode()
    assert 'value="vcard"' not in body
    assert 'value="json"' in body


def test_the_interview_pages_draw_no_calendar_file_link_while_it_is_off(client, user):
    signed_in(client, user)
    on = client.get(reverse("applications:interview_list")).content.decode()
    assert reverse("applications:interview_calendar") in on
    switch_off("format-icalendar")
    off = client.get(reverse("applications:interview_list")).content.decode()
    assert reverse("applications:interview_calendar") not in off


def test_a_cv_cannot_be_downloaded_as_a_format_that_is_off(client, user):
    from postulo.documents.models import CV

    cv = CV.objects.create(owner=user, name="Mine")
    signed_in(client, user)
    url = reverse("documents:cv_download", args=[cv.pk, "docx"])
    assert client.get(url).status_code == 200
    switch_off("format-docx")
    assert client.get(url).status_code == 404
    assert client.get(reverse("documents:cv_download", args=[cv.pk, "odt"])).status_code == 200


# --------------------------------------------------------- the text of an upload


def word_file() -> bytes:
    return docx.write(
        outline.Outline(
            title="Letter",
            language="en",
            blocks=(
                outline.heading("A letter about wombats", 1),
                outline.paragraph("Marsupials of the burrowing sort"),
            ),
        )
    )


def opendocument_file() -> bytes:
    return odt.write(
        outline.Outline(
            title="Letter",
            language="en",
            blocks=(outline.paragraph("Quokkas smile for the camera"),),
        )
    )


def test_the_words_of_a_word_file_are_read():
    text = readers.text_of(io.BytesIO(word_file()), "docx")
    assert "A letter about wombats" in text
    assert "Marsupials of the burrowing sort" in text


def test_the_words_of_an_opendocument_file_are_read():
    assert "Quokkas smile for the camera" in readers.text_of(io.BytesIO(opendocument_file()), "odt")


@pytest.mark.parametrize(
    "data", [b"", b"not a zip", b"PK\x05\x06" + b"\x00" * 18], ids=["empty", "text", "empty zip"]
)
def test_a_file_that_is_not_a_document_has_no_text_and_raises_nothing(data):
    assert readers.text_of(io.BytesIO(data), "docx") == ""
    assert readers.text_of(io.BytesIO(data), "odt") == ""


def test_a_body_with_an_entity_bomb_is_refused_not_expanded():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        b"<w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", bomb)
    assert readers.text_of(io.BytesIO(out.getvalue()), "docx") == ""


def test_a_body_past_the_ceiling_is_not_read(monkeypatch):
    monkeypatch.setattr(readers, "MAX_BODY_BYTES", 10)
    assert readers.text_of(io.BytesIO(word_file()), "docx") == ""


def test_the_text_kept_is_capped(monkeypatch):
    monkeypatch.setattr(readers, "MAX_TEXT_CHARS", 5)
    assert len(readers.text_of(io.BytesIO(word_file()), "docx")) == 5


def upload(user, name, data):
    """Through the form, which is what knows whether the format's module is on."""
    form = UploadedDocumentForm(
        data={"title": "A file", "kind": "cv", "notes": ""},
        files={"file": SimpleUploadedFile(name, data)},
        user=user,
    )
    assert form.is_valid(), form.errors
    document = form.save(commit=False)
    document.owner = user
    document.save()
    return document


def test_an_uploaded_word_file_keeps_its_text_and_is_found_by_it(client, user):
    stored = upload(user, "letter.docx", word_file())
    assert "wombats" in stored.text
    signed_in(client, user)
    found = client.get(reverse("core:search"), {"q": "wombats"}).content.decode()
    assert "A file" in found


def test_an_uploaded_opendocument_file_keeps_its_text(user):
    assert "Quokkas" in upload(user, "letter.odt", opendocument_file()).text


def test_a_file_of_another_kind_has_no_text(user):
    assert upload(user, "letter.txt", b"wombats are here").text == ""


def test_with_the_format_off_the_text_is_not_read_but_the_file_is_kept(user):
    switch_off("format-docx")
    stored = upload(user, "letter.docx", word_file())
    assert stored.text == ""
    with stored.file.open("rb") as handle:
        assert handle.read() == word_file()


def test_the_file_is_kept_exactly_as_it_came(user):
    data = word_file()
    stored = upload(user, "letter.docx", data)
    with stored.file.open("rb") as handle:
        assert handle.read() == data
