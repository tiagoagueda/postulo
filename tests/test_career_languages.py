"""A career entry says the same thing in more than one language (#131).

> also multiple language documents cohabiting should be normal

At the document level that was already true — a CV declares its language and the PDF is laid
out for it. One level down it was not: the career record held one text per field, so a CV
declaring ``fr-fr`` printed English job titles, and the honest way to keep a CV in two
languages was to keep two careers.

Three things are worth holding to here, and they pull in different directions. A second
language must be a **translation**, so a date corrected once is corrected everywhere. A
field that belongs to somebody else — an employer's name, a credential's title — must not be
translatable at all, because offering the box is the harm. And falling back must be
**visible**, or the person meets it in the PDF an employer already has.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.documents.models import CV, CVItem
from postulo.documents.rendering import build_sections, render_cv_html
from postulo.resume import translating
from postulo.resume.models import Certification, Education, Experience, SkillGroup, Translation

pytestmark = pytest.mark.django_db


def an_experience(user, **kwargs) -> Experience:
    values = {
        "organisation": "Weyland-Yutani",
        "role": "Backend engineer",
        "location": "Lisbon",
        "start_date": "2019-01-01",
        "summary": "Kept the services up.",
        "highlights": "Cut deploy time.\nRan the on-call rota.",
    }
    return Experience.objects.create(owner=user, **{**values, **kwargs})


def translate(entry, language: str, **fields) -> None:
    from django.contrib.contenttypes.models import ContentType

    for name, text in fields.items():
        Translation.objects.create(
            owner=entry.owner,
            content_type=ContentType.objects.get_for_model(entry.__class__),
            object_id=entry.pk,
            language=language,
            field=name,
            text=text,
        )


def a_cv_with(user, entry, **kwargs) -> CV:
    from django.contrib.contenttypes.models import ContentType

    cv = CV.objects.create(owner=user, name="For Paris", **kwargs)
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(entry.__class__),
        object_id=entry.pk,
    )
    return cv


# ------------------------------------------------ which fields may be said differently


def test_a_name_that_belongs_to_somebody_else_is_not_translatable():
    """The decision the issue asked for, per field rather than per mechanism.

    *Universidade de Lisboa* stays that on an English CV; translating it invents an employer
    who never existed. Offering the box is what would have caused it.
    """
    assert "organisation" not in translating.fields_for(Experience)
    assert "institution" not in translating.fields_for(Education)
    assert "role" in translating.fields_for(Experience)


def test_a_credential_is_the_awarding_bodys_wording_and_is_left_alone():
    """`Certification` translates nothing at all, which is a statement rather than a gap."""
    assert translating.fields_for(Certification) == ()


def test_a_field_nothing_may_translate_is_dropped_even_if_a_row_exists(user):
    """`TRANSLATABLE` can lose a field; a row nobody can edit should not still print."""
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur")
    Translation.objects.create(
        owner=user,
        content_type=Translation.objects.first().content_type,
        object_id=experience.pk,
        language="fr-fr",
        field="organisation",
        text="Société Weyland",
    )

    overrides = translating.overrides_for(experience, "fr-fr")

    assert overrides == {"role": "Ingénieur"}


# ------------------------------------------------------------ what an entry then says


def test_an_entry_reads_in_the_language_asked_for(user):
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur back-end", location="Lisbonne")

    french = translating.in_language(experience, "fr-fr")

    assert french.role == "Ingénieur back-end"
    assert french.location == "Lisbonne"
    assert french.organisation == "Weyland-Yutani", "not translated, and rightly"
    assert french.start_date == experience.start_date, "and everything else falls through"


def test_untranslated_highlights_are_the_originals_and_translated_ones_are_not(user):
    experience = an_experience(user)

    assert translating.in_language(experience, "fr-fr").highlight_lines == [
        "Cut deploy time.",
        "Ran the on-call rota.",
    ]

    translate(experience, "fr-fr", highlights="Déploiement plus rapide.\nAstreinte tenue.")

    assert translating.in_language(experience, "fr-fr").highlight_lines == [
        "Déploiement plus rapide.",
        "Astreinte tenue.",
    ]


def test_a_translation_somebody_cleared_prints_the_original(user):
    """Blank is withdrawal, not emptiness."""
    experience = an_experience(user)
    translate(experience, "fr-fr", role="   ")

    assert translating.in_language(experience, "fr-fr").role == "Backend engineer"


def test_a_variant_of_the_same_language_is_close_enough(user):
    """A Brazilian reader given European Portuguese has read the entry; given English, not."""
    experience = an_experience(user)
    translate(experience, "pt-pt", role="Engenheiro de backend")

    assert translating.in_language(experience, "pt-br").role == "Engenheiro de backend"
    assert translating.in_language(experience, "de").role == "Backend engineer"


def test_correcting_the_master_copy_corrects_every_language(user):
    """The whole reason this is a translation rather than a second career."""
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur")

    experience.end_date = "2024-06-30"
    experience.save()

    french = translating.in_language(experience, "fr-fr")
    assert str(french.end_date) == "2024-06-30"


# ----------------------------------------------------------------- and what a CV prints


def test_a_cv_in_french_prints_the_french_entry(user):
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur back-end")
    cv = a_cv_with(user, experience, language="fr-fr")

    html = render_cv_html(cv)

    assert "Ingénieur back-end" in html
    assert "Backend engineer" not in html
    assert "Weyland-Yutani" in html, "the employer keeps its name"


def test_a_cv_in_another_language_prints_the_original_rather_than_a_blank(user):
    """A blank line where a job used to be is worse than a line in the wrong language."""
    cv = a_cv_with(user, an_experience(user), language="de")

    assert "Backend engineer" in render_cv_html(cv)


def test_the_language_a_cv_declares_is_the_one_it_reads_in(user):
    """Not the field: a CV naming no language still declares one, and text under a
    declaration of another language is the mismatch this exists to remove.
    """
    experience = an_experience(user)
    translate(experience, "pt-pt", role="Engenheiro")
    user.profile.language = "pt-pt"
    user.profile.save()

    cv = a_cv_with(user, experience, language="")

    assert "Engenheiro" in render_cv_html(cv)


def test_this_cvs_own_highlights_beat_a_translation(user):
    """Somebody who wrote highlights for this CV wrote them for the CV in front of them."""
    experience = an_experience(user)
    translate(experience, "fr-fr", highlights="Traduit.")
    cv = a_cv_with(user, experience, language="fr-fr")
    cv.items.update(override_highlights="Écrit pour ce CV.")

    lines = build_sections(cv)[0].items[0].highlight_lines

    assert lines == ["Écrit pour ce CV."]


def test_a_page_of_entries_costs_one_query_for_their_translations(user, django_assert_num_queries):
    """A generic link has no join to follow, so the batch lookup is what answers for it."""
    entries = [an_experience(user, role=f"Engineer {index}") for index in range(5)]
    for entry in entries:
        translate(entry, "fr-fr", role="Ingénieur")

    with django_assert_num_queries(1):
        translating.overrides_by_entry(entries, "fr-fr")


def test_a_skill_group_prints_its_skills_in_the_cvs_language(user):
    """The one entry a CV prints through another: a skill is only ever on a CV via its group.

    Offering to translate a skill's name and then never printing the translation would be a
    worse promise than not offering it.
    """
    from postulo.resume.models import Skill

    group = SkillGroup.objects.create(owner=user, name="Skills")
    translate(group, "fr-fr", name="Compétences")
    people = Skill.objects.create(owner=user, group=group, name="Team management")
    Skill.objects.create(owner=user, group=group, name="Python")
    translate(people, "fr-fr", name="Gestion d’équipe")
    cv = a_cv_with(user, group, language="fr-fr")

    html = render_cv_html(cv)

    assert "Compétences" in html
    assert "Gestion d’équipe" in html
    assert "Python" in html, "a name that is the same in both stays as it is"


# --------------------------------------------------------------- saying what fell back


def test_a_cv_says_which_entries_have_nothing_in_its_language(user, client):
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur")
    other = an_experience(user, role="Platform engineer", organisation="Aperture")
    cv = a_cv_with(user, experience, language="fr-fr")
    from django.contrib.contenttypes.models import ContentType

    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=other.pk,
    )

    report = translating.fallen_back(cv)

    assert [one.entry for one in report] == [experience, other], "both, for different fields"
    assert "role" not in dict.fromkeys(report[0].fields), "the one field it does have"
    assert "role" in report[1].fields

    client.force_login(user)
    html = client.get(reverse("documents:cv_detail", args=[cv.pk])).content.decode()
    assert "Platform engineer" in html


def test_nothing_is_reported_when_the_cv_is_in_the_language_the_record_is_written_in(user):
    """Otherwise the warning names every entry on the page and is read once, then never."""
    user.profile.record_language = "en-gb"
    user.profile.save()
    cv = a_cv_with(user, an_experience(user), language="en-gb")

    assert translating.fallen_back(cv) == []


def test_a_field_with_nothing_in_it_did_not_fall_back_on_anything(user):
    """Listing an empty summary would bury the two fields that did."""
    experience = an_experience(user, summary="", highlights="")

    missing = translating.fields_that_fell_back(experience, "fr-fr")

    assert set(missing) == {"role", "location"}


def test_the_record_language_falls_back_to_the_one_postulo_is_read_in(user):
    user.profile.language = "pt-pt"
    user.profile.save()

    assert translating.record_language_of(user) == "pt-pt"


# ------------------------------------------------------------------- editing them


def test_the_screen_offers_exactly_the_fields_that_may_be_translated(user, client):
    experience = an_experience(user)
    client.force_login(user)

    html = client.get(
        reverse("resume:item_languages", args=["experience", experience.pk]) + "?language=fr-fr"
    ).content.decode()

    assert 'name="role"' in html
    assert 'name="summary"' in html
    assert 'name="organisation"' not in html, "the employer's own name has no box"


def test_saving_a_translation_writes_it(user, client):
    experience = an_experience(user)
    client.force_login(user)

    client.post(
        reverse("resume:item_languages", args=["experience", experience.pk]),
        {"language": "fr-fr", "role": "Ingénieur", "location": "", "summary": "", "highlights": ""},
    )

    assert translating.in_language(experience, "fr-fr").role == "Ingénieur"


def test_clearing_a_box_leaves_the_row_and_prints_the_original(user, client):
    """A form that deletes rows on save loses work to a mis-click on a field nobody opened."""
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur")
    client.force_login(user)

    client.post(
        reverse("resume:item_languages", args=["experience", experience.pk]),
        {"language": "fr-fr", "role": "", "location": "", "summary": "", "highlights": ""},
    )

    assert translating.in_language(experience, "fr-fr").role == "Backend engineer"
    assert Translation.objects.filter(object_id=experience.pk, field="role").exists()


def test_an_entry_that_translates_nothing_says_so_rather_than_showing_an_empty_form(user, client):
    certification = Certification.objects.create(owner=user, name="AWS SAA", issuer="AWS")
    client.force_login(user)

    html = client.get(
        reverse("resume:item_languages", args=["certification", certification.pk])
    ).content.decode()

    assert "not be the same credential" in html
    assert 'name="name"' not in html


def test_somebody_elses_entry_is_a_404(user, other_user, client):
    experience = an_experience(other_user)
    client.force_login(user)

    response = client.get(reverse("resume:item_languages", args=["experience", experience.pk]))

    assert response.status_code == 404


def test_one_text_per_field_per_language_is_the_databases_rule(user):
    from django.contrib.contenttypes.models import ContentType
    from django.db import IntegrityError, transaction

    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur")

    with pytest.raises(IntegrityError), transaction.atomic():
        Translation.objects.create(
            owner=user,
            content_type=ContentType.objects.get_for_model(Experience),
            object_id=experience.pk,
            language="fr-fr",
            field="role",
            text="Autre",
        )


def test_deleting_an_entry_takes_its_translations(user):
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur")

    experience.delete()

    assert not Translation.objects.exists()


# ------------------------------------------------------------------ and the archive


def an_archive(document):
    """A manifest, wrapped the way `importer.load` expects to be handed one."""
    import json
    import zipfile
    from io import BytesIO

    from postulo.core import export

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_translations_travel_in_the_archive_and_come_back(user, other_user):
    from postulo.core import export, importer

    group = SkillGroup.objects.create(owner=user, name="Languages")
    translate(group, "fr-fr", name="Langues")
    experience = an_experience(user)
    translate(experience, "fr-fr", role="Ingénieur back-end")
    user.profile.record_language = "en-gb"
    user.profile.save()

    document = export.build_document(user)
    rows = document["resume"]["translations"]

    assert {
        "section": "experience",
        "ref": experience.pk,
        "language": "fr-fr",
        "field": "role",
        "text": "Ingénieur back-end",
    } in rows
    assert any(row["section"] == "skill_groups" for row in rows)
    assert document["account"]["profile"]["record_language"] == "en-gb"

    importer.load(other_user, an_archive(document))

    restored = Experience.objects.for_user(other_user).get()
    assert translating.in_language(restored, "fr-fr").role == "Ingénieur back-end"
    other_user.profile.refresh_from_db()
    assert other_user.profile.record_language == "en-gb"


def test_an_archive_written_before_this_still_restores(user, other_user):
    """A career with no translations is what every career was until format 11."""
    from postulo.core import export, importer

    an_experience(user)
    document = export.build_document(user)
    document["resume"].pop("translations")
    document["account"]["profile"].pop("record_language")

    report = importer.load(other_user, an_archive(document))

    assert report.resume_items == 1
    assert not Translation.objects.for_user(other_user).exists()
