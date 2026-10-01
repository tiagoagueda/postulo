"""What Postulo stores, sends and takes in as a language is a tag in its canonical form (#337).

`tests/test_language_tags.py` is about the writer and the comparison. This is about the
places a code lives and the doors it comes through: the columns, the migrations that put
what was already in them right, the archive, a file from elsewhere, a form, an address.

The issue's own measure is at the end, under *Done when*: one person's account read through
every way a code leaves Postulo, and not one of them in the other spelling.
"""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
import json
import re
import zipfile
from io import BytesIO

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import export, importer, languages
from postulo.core.models import SiteSettings
from postulo.documents.models import (
    CV,
    CoverLetter,
    DocumentKind,
    RenderedDocument,
    UploadedDocument,
)
from postulo.resume.models import Experience, Translation

pytestmark = pytest.mark.django_db

MIGRATIONS = {
    "accounts": "postulo.accounts.migrations.0028_a_language_is_a_tag",
    "core": "postulo.core.migrations.0024_a_language_is_a_tag",
    "documents": "postulo.documents.migrations.0013_a_language_is_a_tag",
    "resume": "postulo.resume.migrations.0008_a_language_is_a_tag",
}

#: The column of a document, asked what it does with a value without a document around it.
LANGUAGE = CV._meta.get_field("language")


def migration(app: str):
    return importlib.import_module(MIGRATIONS[app])


def put(model, pk: int, **columns) -> None:
    """Write a row as the database holds it, past the field that would write it properly.

    The columns put a code in its canonical form on the way in, which is the point of them
    and no use to a test that needs a row as it was before they did.
    """
    table = connection.ops.quote_name(model._meta.db_table)
    names = ", ".join(f"{connection.ops.quote_name(name)} = %s" for name in columns)
    values = [
        connection.ops.adapt_datetimefield_value(value) if isinstance(value, dt.datetime) else value
        for value in columns.values()
    ]
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET {names} WHERE id = %s", [*values, pk])  # noqa: S608


def held(model, pk: int, *columns: str) -> tuple:
    """What the database holds, read as it is."""
    table = connection.ops.quote_name(model._meta.db_table)
    names = ", ".join(connection.ops.quote_name(name) for name in columns)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {names} FROM {table} WHERE id = %s", [pk])  # noqa: S608
        return tuple(cursor.fetchone())


def an_experience(user) -> Experience:
    return Experience.objects.create(
        owner=user, organisation="Weyland-Yutani", role="Backend engineer", start_date="2019-01-01"
    )


def a_translation(entry, language: str, field: str, text: str) -> Translation:
    return Translation.objects.create(
        owner=entry.owner,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        language=language,
        field=field,
        text=text,
    )


def an_archive(document: dict) -> zipfile.ZipFile:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def codes_in(document) -> list[str]:
    """Every language code a document holds, wherever in it: each value of a key that says so."""
    found: list[str] = []
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(value, str) and (key == "language" or key.endswith("_language")):
                found.append(value)
            else:
                found.extend(codes_in(value))
    elif isinstance(document, list):
        for item in document:
            found.extend(codes_in(item))
    return found


def bearer(user) -> dict:
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


# ------------------------------------------------------------------- the columns


def test_a_code_is_stored_as_a_tag_however_it_arrives(user):
    profile = user.profile
    profile.language = "pt_br"
    profile.record_language = " EN-gb "
    profile.save()

    assert (profile.language, profile.record_language) == ("pt-BR", "en-GB"), "on the instance"
    assert held(Profile, profile.pk, "language", "record_language") == ("pt-BR", "en-GB")

    Profile.objects.filter(pk=profile.pk).update(language="PT-br")
    assert held(Profile, profile.pk, "language") == ("pt-BR",), "an update is prepared the same"

    cv = CV.objects.create(owner=user, name="For Paris", language="fr_fr")
    assert held(CV, cv.pk, "language") == ("fr-FR",)

    entry = an_experience(user)
    made = Translation.objects.bulk_create(
        [
            Translation(
                owner=user,
                content_type=ContentType.objects.get_for_model(Experience),
                object_id=entry.pk,
                language="zh_hant_tw",
                field="role",
                text="x",
            )
        ]
    )
    assert held(Translation, made[0].pk, "language") == ("zh-Hant-TW",)


def test_a_code_is_found_however_it_is_asked_for(user):
    cv = CV.objects.create(owner=user, name="For Paris", language="fr-FR")

    for asked in ("fr-FR", "fr-fr", "FR_fr"):
        assert CV.objects.for_user(user).filter(language=asked).get() == cv
    assert CV.objects.for_user(user).filter(language__in=["de", "fr_FR"]).get() == cv


def test_the_columns_have_room_for_a_long_tag(user):
    """Ten characters did not hold `ca-ES-valencia`, and PostgreSQL refuses what does not fit."""
    cv = CV.objects.create(owner=user, name="València", language="ca-es-valencia")

    assert held(CV, cv.pk, "language") == ("ca-ES-valencia",)
    for model in (Profile, CV, CoverLetter, UploadedDocument, RenderedDocument, Translation):
        assert model._meta.get_field("language").max_length == languages.MAX_LENGTH == 35
    assert Profile._meta.get_field("record_language").max_length == 35
    assert SiteSettings._meta.get_field("default_language").max_length == 35


@pytest.mark.parametrize("language", ["", "pt-AO", "tlh", "es-419", "sr", "PT_br"])
def test_a_free_field_takes_any_language_shaped_like_one(language):
    """Shape and no more: a document may be in a language Postulo has never heard of."""
    assert LANGUAGE.clean(language, None) == languages.tag(language)


@pytest.mark.parametrize(
    "language", ["english", "not a language", "portuguese-brazil", "<script>", "pt-" + "a" * 40]
)
def test_what_is_not_a_language_is_refused_where_it_is_handed_over(language):
    with pytest.raises(ValidationError) as refused:
        LANGUAGE.clean(language, None)

    assert "is not a language code" in refused.value.messages[0]


# ------------------------------------------------------------------- the migrations


@pytest.mark.parametrize("app", MIGRATIONS)
def test_a_migration_writes_a_tag_as_the_application_does(app):
    """Each migration carries its own copy of the writer, because a migration has to do
    tomorrow what it did the day it was written. Held here to the running one, so that the
    day they part somebody decides it."""
    frozen = migration(app)

    for code in (
        "pt-br",
        "PT_br",
        " en-gb ",
        "fr-FR",
        "de",
        "kab",
        "sr-latn",
        "sr-cyrl",
        "zh-hant-tw",
        "es-419",
        "ca-es-valencia",
        "zh-YUE",
        "en-US-x-AB",
    ):
        assert languages.well_formed(code)
        assert frozen.canonical(code) == languages.tag(code), code
    assert frozen.RENAMED == {"sr": "sr-Cyrl"}
    assert frozen.canonical("sr") == frozen.canonical("SR") == "sr-Cyrl"
    for odd in ("", "   ", " english ", "not a language", "pt-", "pt-" + "a" * 40, None, 7):
        assert not languages.well_formed(odd)
        assert frozen.canonical(odd) == odd, "what is not a tag is left exactly as it is"


def test_the_four_migrations_carry_one_writer():
    writers = {inspect.getsource(migration(app).canonical) for app in MIGRATIONS}
    shapes = {migration(app)._SHAPE.pattern for app in MIGRATIONS}

    assert len(writers) == 1 and len(shapes) == 1


def test_a_profile_s_languages_are_written_properly(user, other_user):
    put(Profile, user.profile.pk, language="pt-br", record_language="PT_pt")
    put(Profile, other_user.profile.pk, language="sr", record_language="english")

    migration("accounts").recase(apps, None)

    assert held(Profile, user.profile.pk, "language", "record_language") == ("pt-BR", "pt-PT")
    assert held(Profile, other_user.profile.pk, "language", "record_language") == (
        "sr-Cyrl",
        "english",
    ), "the old Serbian is the Cyrillic one; what is not a tag is somebody's data, and stays"


def test_an_instance_s_languages_are_written_properly():
    row, _made = SiteSettings.objects.update_or_create(
        pk=1, defaults={"offered_languages": ["en-gb", "pt-br", "PT-BR", "sr", "de", "nonsense!"]}
    )
    put(SiteSettings, row.pk, default_language="fr-fr")

    migration("core").recase(apps, None)

    assert held(SiteSettings, row.pk, "default_language") == ("fr-FR",)
    assert SiteSettings.objects.get(pk=row.pk).offered_languages == [
        "en-GB",
        "pt-BR",
        "sr-Cyrl",
        "de",
        "nonsense!",
    ], "each respelt, and one that is then there twice is there once"


def test_a_document_s_language_is_written_properly_and_what_was_sent_is_not_rewritten(user):
    cv = CV.objects.create(owner=user, name="CV")
    letter = CoverLetter.objects.create(owner=user, name="Letter", body="Dear team")
    upload = UploadedDocument.objects.create(
        owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE
    )
    page = '<html lang="pt-br" dir="ltr"><body>Olá</body></html>'
    sent = RenderedDocument.objects.create(
        owner=user, title="CV", kind=DocumentKind.CV, checksum="abc", source_text=page
    )
    put(CV, cv.pk, language="pt-br")
    put(CoverLetter, letter.pk, language="en_GB")
    put(UploadedDocument, upload.pk, language="sr")
    put(RenderedDocument, sent.pk, language="pt-br")

    migration("documents").recase(apps, None)

    assert held(CV, cv.pk, "language") == ("pt-BR",)
    assert held(CoverLetter, letter.pk, "language") == ("en-GB",)
    assert held(UploadedDocument, upload.pk, "language") == ("sr-Cyrl",)
    assert held(RenderedDocument, sent.pk, "language", "source_text") == ("pt-BR", page), (
        "the column is written properly; the page as it was sent is the record of what was sent"
    )


def test_two_translations_that_turn_out_to_be_one_keep_the_one_edited_last(
    user, other_user, capsys
):
    """`fr-fr` and `fr-FR` were two rows to the constraint and are one translation."""
    entry = an_experience(user)
    early, late = dt.datetime(2026, 1, 1, tzinfo=dt.UTC), dt.datetime(2026, 6, 1, tzinfo=dt.UTC)

    newer = a_translation(entry, "fr-FR", "role", "Ingénieure back-end")
    older = a_translation(entry, "fr-CA", "role", "Ingénieur")
    put(Translation, newer.pk, updated_at=late)
    put(Translation, older.pk, language="fr-fr", updated_at=early)

    # One that still says something, and a later one somebody cleared: the words win.
    said = a_translation(entry, "fr-CA", "summary", "A tenu les services debout.")
    cleared = a_translation(entry, "fr-FR", "summary", "")
    put(Translation, said.pk, language="fr_FR", updated_at=early)
    put(Translation, cleared.pk, updated_at=late)

    alone = a_translation(entry, "pt-PT", "role", "Engenheiro")
    put(Translation, alone.pk, language="pt-br")
    theirs = a_translation(an_experience(other_user), "de", "role", "Ingenieur")
    put(Translation, theirs.pk, language="sr")

    migration("resume").recase(apps, None)

    assert held(Translation, newer.pk, "language", "text") == ("fr-FR", "Ingénieure back-end")
    assert held(Translation, said.pk, "language", "text") == (
        "fr-FR",
        "A tenu les services debout.",
    )
    assert not Translation.objects.filter(pk__in=[older.pk, cleared.pk]).exists()
    assert held(Translation, alone.pk, "language") == ("pt-BR",)
    assert held(Translation, theirs.pk, "language") == ("sr-Cyrl",)
    printed = capsys.readouterr().out
    assert f"translation {older.pk} (" in printed and f"translation {cleared.pk} (" in printed
    assert "2 duplicate translation(s) removed" in printed


def test_a_database_already_written_properly_is_left_alone(user, capsys):
    cv = CV.objects.create(owner=user, name="CV", language="pt-BR")
    a_translation(an_experience(user), "fr-FR", "role", "Ingénieur")

    for app in MIGRATIONS:
        migration(app).recase(apps, None)

    assert held(CV, cv.pk, "language") == ("pt-BR",)
    assert Translation.objects.count() == 1
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------- the ways in


def test_a_translation_is_saved_under_a_tag_and_under_nothing_else(client, user):
    """The screen took its language from the address and saved under whatever it was given:
    `Not A Language At All` was four rows, and a server error on PostgreSQL (#620)."""
    entry = an_experience(user)
    client.force_login(user)
    url = reverse("resume:item_languages", args=["experience", entry.pk])
    boxes = {"role": "Engenheiro", "location": "", "summary": "", "highlights": ""}

    client.post(url, {"language": "pt_br", **boxes})

    assert sorted(set(Translation.objects.values_list("language", flat=True))) == ["pt-BR"]
    saved = Translation.objects.count()

    for nonsense in ("Not A Language At All", "portuguese-brazil", "pt-" + "a" * 40):
        assert client.post(url, {"language": nonsense, **boxes}).status_code == 302
        page = client.get(url, {"language": nonsense})
        assert page.status_code == 200
        assert 'name="role"' not in page.content.decode(), "no form for what is not a language"
    assert Translation.objects.count() == saved, "and nothing saved under one"


def test_a_file_s_locale_is_kept_as_a_tag_or_not_at_all(user, other_user):
    """An importer is a plugin and fills this itself, so it is held to the shape here too:
    what is not a language is left out, where it used to be cut to fit the column."""
    from postulo.resume import importing

    importing.apply(user, importing.Record(locale="pt_br"))
    importing.apply(other_user, importing.Record(locale="Portuguese, as written in Brazil"))

    user.profile.refresh_from_db()
    other_user.profile.refresh_from_db()
    assert user.profile.record_language == "pt-BR"
    assert other_user.profile.record_language == ""


def test_a_menu_keeps_a_language_it_does_not_list(user):
    """A document's language is free and its menu is the instance's own list. A CV in the
    Portuguese of Angola opened on *Follow your profile*, and saving the form wrote that
    over it without anybody having touched the menu."""
    from postulo.accounts.forms import ProfileForm
    from postulo.documents.forms import CoverLetterForm, CVForm, UploadedDocumentForm

    cv = CV.objects.create(owner=user, name="Luanda", language="pt-AO")
    letter = CoverLetter.objects.create(owner=user, name="Carta", body="Boa tarde,", language="tlh")
    upload = UploadedDocument.objects.create(
        owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE, language="es-419"
    )
    user.profile.record_language = "sr"
    user.profile.save()

    for form, code in (
        (CVForm(user=user, instance=cv), "pt-AO"),
        (CoverLetterForm(user=user, instance=letter), "tlh"),
        (UploadedDocumentForm(user=user, instance=upload), "es-419"),
    ):
        menu = str(form["language"])
        assert re.search(rf'<option value="{code}"[^>]*\bselected', menu), menu[-300:]
        assert f'lang="{code}"' in menu, "and says which language it is, as the others do"
    menu = str(ProfileForm(instance=user.profile)["record_language"])
    assert re.search(r'<option value="sr"[^>]*\bselected', menu)

    listed = str(CVForm(user=user, instance=CV(owner=user, language="pt-BR"))["language"])
    assert listed.count('value="pt-BR"') == 1, "one already on the list is not listed again"
    assert re.search(r'<option value="pt-BR"[^>]*\bselected', listed)


def test_saving_a_form_as_it_was_drawn_keeps_the_language(client, user):
    cv = CV.objects.create(owner=user, name="Luanda", language="pt-AO")
    client.force_login(user)
    url = reverse("documents:cv_update", args=[cv.pk])

    form = client.get(url).context["form"]
    drawn = {name: form[name].value() for name in form.fields}
    answer = client.post(
        url, {name: value for name, value in drawn.items() if value not in (None, False)}
    )

    assert answer.status_code == 302, (
        getattr(answer, "context", None) and answer.context["form"].errors
    )
    cv.refresh_from_db()
    assert cv.language == "pt-AO"


def test_the_api_says_a_letter_s_language(client, user):
    """The schema has promised it since #283 and nothing filled it: every letter said blank."""
    CoverLetter.objects.create(owner=user, name="Carta", body="Boa tarde,", language="pt_br")
    CV.objects.create(owner=user, name="Currículo", language="PT-BR")
    token = bearer(user)

    assert client.get("/api/v1/letters", **token).json()["items"][0]["language"] == "pt-BR"
    assert client.get("/api/v1/cvs", **token).json()["items"][0]["language"] == "pt-BR"
    schemas = client.get("/api/v1/openapi.json", **token).json()["components"]["schemas"]
    for name in ("CVOut", "LetterOut", "DocumentOut"):
        assert "BCP 47" in schemas[name]["properties"]["language"]["description"], name
    assert "BCP 47" in schemas["ProfileOut"]["properties"]["record_language"]["description"]


# ------------------------------------------------------------------- a choice on a form


def test_a_language_chosen_on_a_form_is_taken_however_it_is_spelt(client, user):
    """A settings page drawn before the list was respelt posts `pt-br`. It is the choice the
    menu offered, and being told it is not a valid one would be true of nothing anybody did."""
    client.force_login(user)

    answer = client.post(reverse("settings:locale"), {"language": "pt-br", "time_zone": ""})

    assert answer.status_code == 302, answer.context["form"].errors
    assert held(Profile, user.profile.pk, "language") == ("pt-BR",)


def test_the_languages_an_instance_offers_are_taken_however_they_are_spelt():
    from postulo.core.server_forms import OfferedLanguagesForm

    row, _made = SiteSettings.objects.update_or_create(pk=1, defaults={"offered_languages": []})
    form = OfferedLanguagesForm({"offered_languages": ["de", "pt_br", "SR-cyrl"]}, instance=row)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["offered_languages"] == ["de", "pt-BR", "sr-Cyrl"]


def test_a_choice_that_is_not_on_the_menu_is_still_refused():
    from postulo.core.language_field import LanguageChoiceField

    field = LanguageChoiceField(
        choices=[("", "Any"), ("Reviewed", [("pt-BR", "português"), ("de", "Deutsch")])],
        required=False,
    )

    assert field.clean("PT_br") == "pt-BR", "found inside the group it is drawn in"
    assert field.clean("") == ""
    with pytest.raises(ValidationError):
        field.clean("pt-AO")
    with pytest.raises(ValidationError):
        field.clean("not a language")


# ------------------------------------------------------------------- the archive


def an_account(user) -> dict:
    """An account with a code in every place the archive carries one, and its archive."""
    user.profile.language = "sr-Cyrl"
    user.profile.record_language = "pt-BR"
    user.profile.save()
    CV.objects.create(owner=user, name="Pour Paris", language="fr-FR")
    CoverLetter.objects.create(owner=user, name="Lettre", body="Bonjour,", language="fr-FR")
    a_translation(an_experience(user), "fr-FR", "role", "Ingénieur back-end")
    return export.build_document(user)


def test_the_archive_says_which_format_writes_a_tag(user):
    document = an_account(user)

    assert export.FORMAT_VERSION == document["postulo"]["format"] == 28
    assert sorted(set(codes_in(document))) == ["fr-FR", "pt-BR", "sr-Cyrl"]


def test_an_older_archive_is_read_and_its_codes_are_respelt(user, other_user):
    """Format 27 and before wrote `pt-br`, and called Serbian `sr`."""
    document = an_account(user)
    document["postulo"]["format"] = 27
    document["account"]["profile"]["language"] = "sr"
    document["account"]["profile"]["record_language"] = "pt-br"
    document["documents"]["cvs"][0]["language"] = "fr-fr"
    document["documents"]["cover_letters"][0]["language"] = "sr"
    document["resume"]["translations"][0]["language"] = "fr_FR"

    report = importer.load(other_user, an_archive(document))

    other_user.profile.refresh_from_db()
    assert (other_user.profile.language, other_user.profile.record_language) == ("sr-Cyrl", "pt-BR")
    assert CV.objects.for_user(other_user).get().language == "fr-FR"
    assert CoverLetter.objects.for_user(other_user).get().language == "sr-Cyrl"
    assert Translation.objects.for_user(other_user).get().language == "fr-FR"
    assert not report.skipped


def test_a_newer_archive_keeps_a_serbian_that_says_no_script(user, other_user):
    """From format 28 on a bare `sr` is what somebody declared about a document, which is a
    free field: Serbian, in a script they did not say. Only the old list's `sr` is renamed."""
    document = an_account(user)
    document["documents"]["cvs"][0]["language"] = "sr"

    importer.load(other_user, an_archive(document))

    assert CV.objects.for_user(other_user).get().language == "sr"


def test_what_an_archive_calls_a_language_and_is_not_one_is_left_blank_and_said(user, other_user):
    """An archive is a file anybody can edit. The rest of it is restored."""
    document = an_account(user)
    document["account"]["profile"]["record_language"] = "the Portuguese of Brazil"
    document["documents"]["cvs"][0]["language"] = "x" * 60
    document["resume"]["translations"][0]["language"] = "not a language"

    report = importer.load(other_user, an_archive(document))

    other_user.profile.refresh_from_db()
    assert other_user.profile.record_language == ""
    assert CV.objects.for_user(other_user).get().language == ""
    assert not Translation.objects.for_user(other_user).exists()
    assert Experience.objects.for_user(other_user).count() == 1, "the entry itself is restored"
    said = [line for line in report.skipped if "is not a language code" in line]
    assert len(said) == 3, report.skipped


# ------------------------------------------------------------------- done when


def test_done_when_nothing_postulo_writes_is_in_the_other_spelling(client, user, monkeypatch):
    """The issue's own measure: the list, the stored columns, the API, the archive, the
    webhook payload and a rendered document, read for one account whose every code was typed
    or imported the other way."""
    from postulo.documents import rendering
    from postulo.notifications import service
    from postulo.notifications.base import Notification
    from postulo.plugins.webhook import payload_for

    # The list.
    for code in dict(settings.LANGUAGES):
        assert languages.tag(code) == code
    assert {"en-GB", "fr-FR", "pt-PT", "pt-BR", "sr-Cyrl"} <= set(dict(settings.LANGUAGES))
    assert settings.LANGUAGE_CODE == "en-GB"

    # Typed and imported the other way.
    profile = user.profile
    profile.language, profile.record_language = "pt_br", "PT-br"
    profile.save()
    cv = CV.objects.create(owner=user, name="Currículo", language="pt-br")
    letter = CoverLetter.objects.create(
        owner=user, name="Carta", body="Boa tarde,", language="PT_BR"
    )
    a_translation(an_experience(user), "fr_fr", "role", "Ingénieur")

    # The stored columns.
    assert held(Profile, profile.pk, "language", "record_language") == ("pt-BR", "pt-BR")
    assert held(CV, cv.pk, "language") == held(CoverLetter, letter.pk, "language") == ("pt-BR",)
    assert list(Translation.objects.values_list("language", flat=True)) == ["fr-FR"]

    # The API.
    token = bearer(user)
    assert client.get("/api/v1/cvs", **token).json()["items"][0]["language"] == "pt-BR"
    assert client.get("/api/v1/letters", **token).json()["items"][0]["language"] == "pt-BR"
    assert client.get("/api/v1/profile", **token).json()["record_language"] == "pt-BR"

    # The archive.
    assert sorted(set(codes_in(export.build_document(user)))) == ["fr-FR", "pt-BR"]

    # The webhook payload.
    sent: list[Notification] = []
    monkeypatch.setattr(
        service, "_deliver", lambda user, notification: sent.append(notification) or 1
    )
    service.notify(user, Notification(event="reminder_due", title="x", body="", url="/"))
    assert payload_for(sent[0])["language"] == "pt-BR"

    # A rendered document, which is where a PDF takes its language.
    assert '<html lang="pt-BR"' in rendering.render_cv_html(cv)
    assert '<html lang="pt-BR"' in rendering.render_letter_html(letter, None)

    # And the interface itself.
    client.force_login(user)
    page = client.get(reverse("core:home"))
    assert '<html lang="pt-BR"' in page.content.decode()
    assert page.headers["Content-Language"] == "pt-BR"
