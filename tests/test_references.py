"""References: the people who will vouch for you, kept as contacts, printed when agreed (#696).

The person is a contact, so the instance's export, erasure, retention and merge reach a
referee. A CV prints one only once the person has agreed, and then prints a value built from
the entry rather than the entry or the contact.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.template import Context, Template
from django.urls import reverse
from django.utils import translation

from postulo.core import export, gdpr, importer
from postulo.core.models import PhoneNumber
from postulo.documents import docx, formats, rendering
from postulo.documents.forms import AddCVItemsForm
from postulo.documents.models import CV, CVItem
from postulo.jobs import merging
from postulo.jobs.models import Company, Contact
from postulo.resume.forms import ReferenceForm
from postulo.resume.models import Reference
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db

LINE_AGREED = "Chell · Line manager, Aperture · Line manager 2019 to 2022"


def a_contact(user, name="Chell", **fields) -> Contact:
    company, _made = Company.objects.get_or_create(owner=user, name="Aperture")
    return Contact.objects.create(
        owner=user, name=name, company=company, role="Line manager", **fields
    )


def a_reference(user, contact=None, **fields) -> Reference:
    contact = contact or a_contact(user)
    return Reference.objects.create(
        owner=user,
        contact=contact,
        **{"relationship": "Line manager 2019 to 2022", **fields},
    )


def on_a_cv(user, *entries, **cv_fields) -> CV:
    cv = CV.objects.create(owner=user, **{"name": "Main", "language": "en-GB", **cv_fields})
    for order, entry in enumerate(entries):
        CVItem.objects.create(
            owner=user,
            cv=cv,
            content_type=ContentType.objects.get_for_model(type(entry)),
            object_id=entry.pk,
            order=order,
        )
    return cv


# ------------------------------------------------------------------- the record


def test_the_section_is_last_and_has_its_own_slug():
    assert OVERVIEW_ORDER[-1] == "reference"
    assert SECTIONS["reference"].model is Reference
    assert str(SECTIONS["reference"].plural) == "References"


def test_a_reference_asks_nobody_and_prints_no_details_until_told(user):
    mine = a_reference(user)
    assert mine.permission == "not_asked"
    assert mine.show_details is False
    assert mine.is_agreed is False


def test_create_edit_move_and_delete_through_the_registry(client, user):
    client.force_login(user)
    one, two = a_contact(user, "Chell"), Contact.objects.create(owner=user, name="Wheatley")
    created = client.post(
        reverse("resume:item_create", args=["reference"]),
        {"contact": one.pk, "relationship": "Manager", "permission": "asked"},
    )
    assert created.status_code == 302
    client.post(
        reverse("resume:item_create", args=["reference"]),
        {"contact": two.pk, "permission": "not_asked"},
    )
    first, second = Reference.objects.for_user(user).order_by("order", "pk")
    assert (first.contact, first.permission, first.show_details) == (one, "asked", False)
    assert second.permission == "not_asked"

    edited = client.post(
        reverse("resume:item_update", args=["reference", first.pk]),
        {"contact": one.pk, "relationship": "Mentor", "permission": "agreed", "show_details": "on"},
    )
    assert edited.status_code == 302
    first.refresh_from_db()
    assert (first.relationship, first.permission, first.show_details) == ("Mentor", "agreed", True)

    client.post(reverse("resume:item_move", args=["reference", second.pk, "up"]))
    assert list(Reference.objects.for_user(user)) == [second, first]

    gone = client.post(reverse("resume:item_delete", args=["reference", first.pk]))
    assert gone.status_code == 302
    assert not Reference.objects.filter(pk=first.pk).exists()
    assert Contact.objects.filter(pk=one.pk).exists(), "the person stays a contact"


def test_the_overview_says_the_permission_in_words(client, user):
    a_reference(user, permission="asked")
    client.force_login(user)
    html = client.get(reverse("resume:overview")).content.decode()
    assert '<section id="section-reference">' in html
    assert "Asked" in html
    assert "Not printed on any CV until they have agreed." in html

    Reference.objects.update(permission="agreed", show_details=True)
    html = client.get(reverse("resume:overview")).content.decode()
    assert "Printed with their email address and telephone number." in html


def test_the_form_lists_only_my_contacts_and_refuses_another_accounts(user, other_user):
    mine = a_contact(user)
    theirs = a_contact(other_user, "Glados")
    form = ReferenceForm(user=user)
    assert list(form.fields["contact"].queryset) == [mine]
    refused = ReferenceForm({"contact": theirs.pk, "permission": "agreed"}, user=user)
    assert not refused.is_valid()
    assert "contact" in refused.errors


def test_the_save_refuses_another_accounts_contact_too(user, other_user):
    theirs = a_contact(other_user, "Glados")
    with pytest.raises(ValidationError):
        Reference.objects.create(owner=user, contact=theirs)
    assert not Reference.objects.exists()


def test_one_entry_per_person_in_the_form_and_in_the_database(user):
    person = a_contact(user)
    a_reference(user, person)
    again = ReferenceForm({"contact": person.pk, "permission": "asked"}, user=user)
    assert not again.is_valid()
    assert "already one of your references" in str(again.errors["contact"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Reference.objects.create(owner=user, contact=person)


def test_the_form_edits_an_entry_without_clashing_with_itself(user):
    mine = a_reference(user)
    form = ReferenceForm(
        {"contact": mine.contact_id, "permission": "agreed"}, user=user, instance=mine
    )
    assert form.is_valid(), form.errors


def test_the_page_links_to_the_contact_which_returns_here(client, user):
    mine = a_reference(user)
    client.force_login(user)
    page = reverse("resume:item_update", args=["reference", mine.pk])
    html = client.get(page).content.decode()
    assert "Edit their details" in html
    assert "Add a person who is not a contact yet" in html

    contact_page = f"{reverse('jobs:contact_update', args=[mine.contact_id])}?next={page}"
    assert client.get(contact_page).context["return_to"] == page
    saved = client.post(contact_page, {"name": "Chell Johnson", "role": "Boss", "next": page})
    assert saved.status_code == 302 and saved.url == page


def test_the_contact_form_does_not_follow_an_address_on_another_host(client, user):
    mine = a_contact(user)
    client.force_login(user)
    url = reverse("jobs:contact_update", args=[mine.pk])
    saved = client.post(url, {"name": "Chell", "next": "https://evil.example/"})
    assert saved.status_code == 302 and "evil.example" not in saved.url


# ------------------------------------------------------------------ what a CV prints


def test_a_reference_that_is_not_agreed_prints_nothing_name_included(user):
    for permission in ("not_asked", "asked"):
        mine = a_reference(user, permission=permission)
        cv = on_a_cv(user, mine)
        assert "Chell" not in rendering.render_cv_html(cv)
        assert "Chell" not in rendering.cv_text(cv)
        assert "References" not in rendering.cv_text(cv), "no heading over nobody"
        with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as word:
            assert "Chell" not in word.read("word/document.xml").decode()
        mine.delete()
        cv.delete()


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_an_agreed_one_prints_name_role_company_and_relationship_alone(user, theme, kind):
    contact = a_contact(user, email="chell@aperture.example")
    PhoneNumber.objects.create(owner=user, holder=contact, number="+351912345678", is_primary=True)
    cv = on_a_cv(user, a_reference(user, contact, permission="agreed"), theme=theme, kind=kind)

    html = rendering.render_cv_html(cv)
    text = rendering.cv_text(cv)
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as word:
        document = word.read("word/document.xml").decode()

    assert "References" in html
    for where in (html, text, document):
        assert LINE_AGREED in where.replace("&amp;", "&")
        assert "chell@aperture.example" not in where
        assert "912" not in where


@pytest.mark.parametrize("theme", ["plain", "classic"])
def test_the_details_print_when_the_entry_says_so_in_every_format(user, theme):
    contact = a_contact(user, email="chell@aperture.example")
    PhoneNumber.objects.create(owner=user, holder=contact, number="+351912345678", is_primary=True)
    cv = on_a_cv(
        user, a_reference(user, contact, permission="agreed", show_details=True), theme=theme
    )

    text = rendering.cv_text(cv)
    html = rendering.render_cv_html(cv)
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as word:
        document = word.read("word/document.xml").decode()

    line = next(line for line in text.splitlines() if "Chell" in line)
    assert "chell@aperture.example" in line and "912 345 678" in line
    assert line.removeprefix(formats.BULLET) in html
    assert line.removeprefix(formats.BULLET) in document


def test_details_are_not_printed_where_agreement_is_missing_whatever_show_details_says(user):
    contact = a_contact(user, email="chell@aperture.example")
    cv = on_a_cv(user, a_reference(user, contact, permission="asked", show_details=True))
    assert "chell@aperture.example" not in rendering.render_cv_html(cv)


def test_a_theme_is_handed_a_value_and_never_the_entry_or_the_contact(user):
    contact = a_contact(user, email="chell@aperture.example")
    cv = on_a_cv(user, a_reference(user, contact, permission="agreed"))
    (section,) = rendering.build_sections(cv)
    (entry,) = section.items

    assert entry.cv_item is None, "nothing to walk back to the entry from"
    assert isinstance(entry.item, rendering.Referee)
    assert not hasattr(entry.item, "contact") and not hasattr(entry.item, "owner")
    with pytest.raises(Exception):  # noqa: B017 -- frozen: FrozenInstanceError
        entry.item.email = "x@example.org"
    assert entry.item.email == "" and entry.item.phone == ""

    # A template of a plugin's own, written against the contact, reads nothing.
    leaked = Template("{% if entry.item.contact %}LEAK{% endif %}{{ entry.item.contact.email }}")
    assert leaked.render(Context({"entry": entry})) == ""


def test_the_picker_shows_the_entry_with_the_reason_and_the_cv_page_names_what_is_left_out(
    client, user
):
    mine = a_reference(user, permission="asked")
    cv = CV.objects.create(owner=user, name="Main", language="en-GB")
    label = dict(AddCVItemsForm(cv=cv).fields["add_reference"].choices)[mine.pk]
    assert "Chell" in label and "not printed" in label and "Asked" in label

    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Reference),
        object_id=mine.pk,
    )
    client.force_login(user)
    html = client.get(reverse("documents:cv_detail", args=[cv.pk])).content.decode()
    assert "data-left-out" in html
    assert "left off, because they have not agreed" in html

    Reference.objects.update(permission="agreed")
    html = client.get(reverse("documents:cv_detail", args=[cv.pk])).content.decode()
    assert "data-left-out" not in html


def test_a_cv_with_no_reference_on_it_is_what_it_was(user):
    """Byte for byte: nothing new is drawn for a CV that names none and says nothing."""
    cv = CV.objects.create(owner=user, name="Plain", language="en-GB")
    with_one_unagreed = on_a_cv(user, a_reference(user), language="en-GB", name="Other")
    assert "references" not in rendering.render_cv_html(cv).lower()
    assert rendering.render_cv_html(cv).replace("Plain", "") == (
        rendering.render_cv_html(with_one_unagreed).replace("Other", "")
    )
    assert rendering.cv_text(cv) == rendering.cv_text(with_one_unagreed)


# ------------------------------------------------- "References are available on request."


def test_the_switch_is_off_by_default_and_is_in_the_form_the_archive_and_the_api(client, user):
    from postulo.documents import printing
    from postulo.documents.forms import CVForm

    assert CV._meta.get_field("references_on_request").default is False
    assert printing.SWITCHES["references_on_request"] == "references_on_request"
    assert "references_on_request" in CVForm(user=user).fields
    cv = on_a_cv(user, references_on_request=True)
    document = export.build_document(user)
    assert document["documents"]["cvs"][0]["prints"]["references_on_request"] is True
    assert document["documents"]["cvs"][0]["id"] == cv.pk

    from postulo.api.routers.documents import _prints_out

    assert _prints_out(cv, offered=False)["references_on_request"] is True


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_the_sentence_prints_once_in_every_format_with_or_without_references(user, theme, kind):
    sentence = "References are available on request."
    bare = on_a_cv(user, references_on_request=True, theme=theme, kind=kind)
    listed = on_a_cv(
        user,
        a_reference(user, permission="agreed"),
        name="Listed",
        references_on_request=True,
        theme=theme,
        kind=kind,
    )
    for cv in (bare, listed):
        assert rendering.render_cv_html(cv).count(sentence) == 1
        assert rendering.cv_text(cv).count(sentence) == 1
        with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as word:
            assert word.read("word/document.xml").decode().count(sentence) == 1
    assert rendering.cv_text(bare).strip().endswith(sentence)
    assert "Chell" not in rendering.cv_text(bare), "it names nobody"


def test_the_sentence_is_set_in_the_documents_language(user, monkeypatch):
    from django.utils.functional import lazy

    said = lazy(
        lambda: (
            "Références sur demande."
            if (translation.get_language() or "").startswith("fr")
            else "English."
        ),
        str,
    )
    monkeypatch.setattr(rendering, "REFERENCES_ON_REQUEST", said())
    cv = on_a_cv(user, references_on_request=True, language="fr")
    assert rendering.cv_text(cv).strip().endswith("Références sur demande.")


# --------------------------------------------------------------- the contact's rights


def test_the_contacts_document_carries_the_reference_including_the_note(user):
    mine = a_reference(user, permission="agreed", note="Asked over coffee")
    document = gdpr.contact_document(mine.contact)
    assert document["version"] == gdpr.DOCUMENT_VERSION == 6
    assert document["references"] == [
        {
            "relationship": "Line manager 2019 to 2022",
            "permission": "agreed",
            "prints_details": False,
            "note": "Asked over coffee",
        }
    ]


def test_the_erasure_deletes_counts_and_says_sent_copies_are_kept(user):
    mine = a_reference(user, permission="agreed")
    cv = on_a_cv(user, mine)
    report = gdpr.erase_contact(mine.contact)
    assert report.deleted["references"] == 1
    assert "1 entry among your references" in report.summary()
    assert "already sent keeps them, as it was sent" in report.summary()
    assert not Reference.objects.exists()
    assert not cv.items.exists(), "its place on a CV goes with it"


def test_an_erasure_with_no_reference_does_not_talk_about_sent_copies(user):
    report = gdpr.erase_contact(Contact.objects.create(owner=user, name="Nobody"))
    assert "sent" not in report.summary()


def test_the_retention_dry_run_lists_them(user, settings):
    from postulo.core.models import SiteSettings

    mine = a_reference(user)
    Contact.objects.filter(pk=mine.contact_id).update(created_at="2000-01-01T00:00:00Z")
    row = SiteSettings.get()
    row.retention_days = 30
    row.save()
    would = gdpr.retention_dry_run()["would_remove"]
    assert would["references"] == 1


def test_deleting_a_company_counts_the_referees_among_its_contacts(client, user):
    mine = a_reference(user)
    client.force_login(user)
    url = reverse("jobs:company_delete", args=[mine.contact.company_id])
    html = client.get(url).content.decode()
    assert "1 entry among your references" in html
    client.post(url)
    assert not Reference.objects.exists()


def test_a_merge_moves_the_entry_to_the_kept_person(user):
    kept = Contact.objects.create(owner=user, name="Chell")
    other = Contact.objects.create(owner=user, name="Chell J.")
    mine = a_reference(user, other, permission="agreed")
    plan = merging.plan_contacts(kept, other)
    assert any("references" in move.label for move in plan.moves)
    merging.merge_contacts(kept, other)
    mine.refresh_from_db()
    assert mine.contact == kept and mine.permission == "agreed"
    assert not Contact.objects.filter(pk=other.pk).exists()


def test_a_merge_keeps_the_kept_ones_entry_when_both_are_references_and_says_so(user):
    kept = Contact.objects.create(owner=user, name="Chell")
    other = Contact.objects.create(owner=user, name="Chell J.")
    ours = a_reference(user, kept, permission="agreed")
    theirs = a_reference(user, other, permission="asked")
    cv = on_a_cv(user, theirs)

    plan = merging.plan_contacts(kept, other)
    assert any("already have one" in line for line in plan.left_behind)
    merging.merge_contacts(kept, other)

    assert list(Reference.objects.all()) == [ours]
    ours.refresh_from_db()
    assert ours.permission == "agreed"
    assert not cv.items.exists()


# ----------------------------------------------------------------------- the archive


def read_archive(user):
    document = export.build_document(user)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    return zipfile.ZipFile(buffer), document


def test_the_archive_round_trips_a_reference_with_its_contact_and_its_cv(user, other_user):
    contact = a_contact(user, email="chell@aperture.example")
    mine = a_reference(user, contact, permission="agreed", show_details=True, note="Yes")
    on_a_cv(user, mine, references_on_request=True)
    archive, document = read_archive(user)

    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 57
    (written,) = document["resume"]["references"]
    assert written["contact_id"] == contact.pk and written["permission"] == "agreed"

    importer.load(other_user, archive)

    (back,) = Reference.objects.for_user(other_user)
    assert back.contact.owner == other_user and back.contact.name == "Chell"
    assert (back.permission, back.show_details, back.note) == ("agreed", True, "Yes")
    (cv,) = CV.objects.for_user(other_user)
    assert [item.item for item in cv.items.all()] == [back]
    assert cv.references_on_request is True


def test_an_older_archive_without_references_still_loads(user, other_user):
    a_reference(user)
    _archive, document = read_archive(user)
    del document["resume"]["references"]
    document["postulo"]["format"] = 55
    for cv in document["documents"]["cvs"]:
        cv.get("prints", {}).pop("references_on_request", None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as older:
        older.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    report = importer.load(other_user, zipfile.ZipFile(buffer))
    assert not Reference.objects.for_user(other_user).exists()
    assert report is not None


def test_a_reference_whose_contact_is_not_in_the_file_is_skipped_and_named(user, other_user):
    a_reference(user, permission="agreed")
    _archive, document = read_archive(user)
    document["resume"]["references"][0]["contact_id"] = 999_999
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as changed:
        changed.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    report = importer.load(other_user, zipfile.ZipFile(buffer))
    assert not Reference.objects.for_user(other_user).exists()
    assert any("its contact is not in the file" in line for line in report.skipped)


def test_a_file_cannot_make_a_reference_agreed_by_saying_anything_else(user, other_user):
    a_reference(user, permission="agreed")
    _archive, document = read_archive(user)
    document["resume"]["references"][0]["permission"] = "yes please"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as changed:
        changed.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))
    assert Reference.objects.for_user(other_user).get().permission == "not_asked"


def test_the_candidate_file_carries_no_reference_and_no_contact(user):
    a_reference(user, permission="agreed")
    document = export.build_candidate_document(user)
    assert "references" not in document["resume"]
    assert "Chell" not in json.dumps(document)
    assert "contacts" not in document and "companies" not in document


# ------------------------------------------------------------------------- the API


def test_the_api_reads_and_writes_the_switch(client, user):
    from postulo.api.models import ApiToken

    cv = on_a_cv(user)
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read", "write"))
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    body = client.get(f"/api/v1/cvs/{cv.pk}", **headers).json()
    assert body["prints"]["references_on_request"] is False

    changed = client.patch(
        f"/api/v1/cvs/{cv.pk}",
        data=json.dumps({"prints": {"references_on_request": True}}),
        content_type="application/json",
        **headers,
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["prints"]["references_on_request"] is True
    cv.refresh_from_db()
    assert cv.references_on_request is True
