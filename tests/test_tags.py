"""A tag's colour and its icon: the palette, the guards, and the pill on every page (#285).

The bug this feature came out of is worth stating, because most of these tests are about it
rather than about colour. `Tag.colour` existed from the first migration and *nothing read
it*: the tags page let somebody type "amber", and the board, the table and the application
page all drew the same grey pill. So the tests that matter most here are the ones that open
a page and look at the class on the tag, not the ones that check a model field round-trips.
"""

from __future__ import annotations

import importlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.template import Context, engines
from django.urls import reverse

from postulo.applications.forms import TagForm
from postulo.applications.models import Application, Status
from postulo.core import export as export_module
from postulo.core import importer
from postulo.core.models import NEAREST_TONE, Tag, TagColour, TagIcon, nearest_tone
from postulo.jobs.models import Company, JobPosting

REPO = Path(__file__).resolve().parents[1]
CSS = (REPO / "assets" / "css" / "app.css").read_text(encoding="utf-8")
ICON_LIST = REPO / "assets" / "icons.txt"

pytestmark = pytest.mark.django_db


def listed_icons() -> set[str]:
    return {
        line.split("#", 1)[0].strip()
        for line in ICON_LIST.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }


def application_for(user, tags):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Chamber Lead")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    application.tags.set(tags)
    return application


# --------------------------------------------------------- the palette exists at all


@pytest.mark.parametrize("colour", [c.value for c in TagColour])
def test_every_colour_a_tag_may_take_is_painted(colour):
    """A choice with no rule behind it draws an unstyled pill, which reads as a bug in the
    stylesheet rather than a missing line in it. A tone is added in both places or neither."""
    assert f".tag-{colour} {{" in CSS, f"no .tag-{colour} rule in app.css"


@pytest.mark.parametrize("icon", [i.value for i in TagIcon if i.value])
def test_every_icon_a_tag_may_take_is_vendored(icon):
    """`{% icon %}` raises on a name it has no file for, and these names arrive from a
    database row rather than from a template, so an unvendored choice is a 500 on a board."""
    assert icon in listed_icons(), f"add {icon} to assets/icons.txt and run `npm run sync:icons`"


@pytest.mark.parametrize("colour", [c.value for c in TagColour])
def test_every_colour_moves_the_preview_on_the_form(colour):
    assert f'[name="colour"][value="{colour}"]:checked' in CSS


@pytest.mark.parametrize("icon", [i.value for i in TagIcon if i.value])
def test_every_icon_moves_the_preview_on_the_form(icon):
    """Written out one pairing at a time, because CSS cannot match one attribute against
    another. This is the check that the choices grew and the stylesheet did not."""
    assert f'[name="icon"][value="{icon}"]:checked) .tag-preview [data-icon="{icon}"]' in CSS


def test_the_pill_keeps_a_shape_when_the_theme_throws_the_tint_away():
    """A high-contrast theme discards `background-color`, which is all seven tones (#277)."""
    forced = CSS[CSS.index("Forced colours, beyond the fields") :]
    assert re.search(r"\.tag \{\s*border: 1px solid CanvasText;", forced)


def test_the_gallery_shows_the_colours_a_tag_can_actually_be():
    from postulo.core import design

    assert design.TAGS == tuple(colour.value for colour in TagColour)


# ------------------------------------------------------------- reading what is stored


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("sky", "blue"),
        ("SLATE", "grey"),
        (" emerald ", "green"),
        ("purple", "violet"),
        ("orange", "amber"),
        ("crimson", "rose"),
        ("turquoise", "teal"),
        ("", "grey"),
        ("#f59e0b", "grey"),
        ("orange-ish", "grey"),
    ],
)
def test_a_typed_colour_becomes_the_nearest_one_that_exists(typed, expected):
    assert nearest_tone(typed) == expected


def test_no_synonym_points_at_a_colour_that_is_not_in_the_palette():
    assert set(NEAREST_TONE.values()) <= {colour.value for colour in TagColour}


def test_a_tag_holding_nonsense_draws_as_a_plain_one(user):
    """The second guard. The migration cleans the rows that exist; an archive written by a
    newer Postulo, or a fixture, can still hand this one a word it has never heard of."""
    tag = Tag(owner=user, name="Imported", colour="chartreuse", icon="unicorn")
    assert tag.tone == "tag-grey"
    assert tag.glyph == ""


def test_a_tag_says_how_it_is_drawn(user):
    tag = Tag(owner=user, name="Dream job", colour=TagColour.AMBER, icon=TagIcon.STAR)
    assert tag.tone == "tag-amber"
    assert tag.glyph == "star"


def test_a_tag_is_grey_and_iconless_until_somebody_says_otherwise(user):
    tag = Tag.objects.create(owner=user, name="Remote")
    assert tag.colour == TagColour.GREY
    assert tag.icon == ""


def test_a_tag_the_api_invents_is_grey_and_iconless(user):
    """The API takes and gives names and nothing else: a tag made by a script has no opinion
    about how it should look, and inventing one for it would be inventing data."""
    (tag,) = Tag.named(user, ["Via the API"])
    assert (tag.colour, tag.icon) == (TagColour.GREY, "")


# --------------------------------------------------------------------- the component


def render_tag(tag) -> str:
    """A string never reaches the loader that compiles `<c-tag …>`, so the native tag stands
    in for it here; the page tests below exercise the compiled form."""
    engine = engines["django"].engine
    return engine.from_string('{% cotton tag :tag="tag" / %}').render(Context({"tag": tag}))


def test_the_component_draws_the_colour_the_icon_and_the_word(user):
    tag = Tag(owner=user, name="Dream job", colour=TagColour.AMBER, icon=TagIcon.STAR)
    html = render_tag(tag)
    assert 'class="tag tag-amber"' in html
    assert 'data-icon="star"' in html
    assert "<bdi>Dream job</bdi>" in html


def test_the_icon_is_never_the_only_thing_said(user):
    """#274's rule: the word carries the meaning, and the picture decorates it."""
    tag = Tag(owner=user, name="Relocation", colour=TagColour.ROSE, icon=TagIcon.MAP_PIN)
    html = render_tag(tag)
    assert 'aria-hidden="true"' in html
    assert "Relocation" in html


def test_a_tag_with_no_icon_draws_no_icon(user):
    html = render_tag(Tag(owner=user, name="Backup plan"))
    assert "<svg" not in html
    assert 'class="tag tag-grey"' in html


# -------------------------------------------------- everywhere a tag is actually drawn


@pytest.mark.parametrize(
    "query",
    [{}, {"view": "board"}, {"view": "table", "columns": "role,tags"}],
)
def test_the_colour_reaches_the_lists(client, user, query):
    tag = Tag.objects.create(
        owner=user, name="Dream job", colour=TagColour.AMBER, icon=TagIcon.STAR
    )
    application_for(user, [tag])
    client.force_login(user)

    html = client.get(reverse("applications:list"), query).content.decode()

    assert "tag tag-amber" in html, "the board and the table drew a grey pill before #285"
    assert 'data-icon="star"' in html


def test_the_colour_reaches_the_application_and_the_tags_page(client, user):
    tag = Tag.objects.create(owner=user, name="Remote", colour=TagColour.BLUE, icon=TagIcon.HOME)
    application = application_for(user, [tag])
    client.force_login(user)

    detail = client.get(application.get_absolute_url()).content.decode()
    assert "tag tag-blue" in detail and 'data-icon="home"' in detail

    listing = client.get(reverse("applications:tag_list")).content.decode()
    assert "tag tag-blue" in listing and 'data-icon="home"' in listing


# ----------------------------------------------------------------------- choosing one


def test_the_form_offers_swatches_rather_than_two_lists_of_words(client, user):
    client.force_login(user)
    html = client.get(reverse("applications:tag_create")).content.decode()

    assert "<select" not in html, "a colour is not a word in a dropdown"
    assert html.count('type="radio"') == len(TagColour.choices) + len(TagIcon.choices)
    for colour in TagColour:
        assert f'class="tag tag-{colour.value}"' in html, f"{colour.value} is not shown as itself"
    assert "tag-preview" in html


def test_the_form_previews_the_tag_the_person_is_making(client, user):
    tag = Tag.objects.create(owner=user, name="Dream job", colour=TagColour.VIOLET)
    client.force_login(user)

    html = client.get(reverse("applications:tag_update", args=[tag.pk])).content.decode()

    assert "tag-preview" in html and "<bdi>Dream job</bdi>" in html
    # Every icon is in the preview; the stylesheet folds away the ones not chosen.
    for icon in (i.value for i in TagIcon if i.value):
        assert f'data-icon="{icon}"' in html


def test_the_form_does_not_offer_a_third_answer_to_a_two_answer_question(user):
    """A `ModelForm` adds `---------` to a field that allows blank, and both of these say
    what blank means already: grey, and "No icon"."""
    form = TagForm(user=user)
    assert [value for value, _ in form.fields["colour"].choices] == [c.value for c in TagColour]
    assert [value for value, _ in form.fields["icon"].choices] == [i.value for i in TagIcon]
    assert "---------" not in str(form)


def test_choosing_a_colour_and_an_icon_saves_them(user):
    form = TagForm(
        data={"name": "Dream job", "colour": TagColour.VIOLET, "icon": TagIcon.STAR}, user=user
    )
    assert form.is_valid(), form.errors
    tag = form.save(commit=False)
    tag.owner = user
    tag.save()
    assert (tag.colour, tag.icon) == (TagColour.VIOLET, TagIcon.STAR)


def test_a_colour_off_the_palette_is_refused(user):
    form = TagForm(data={"name": "Dream job", "colour": "chartreuse", "icon": ""}, user=user)
    assert not form.is_valid()
    assert "colour" in form.errors


def test_an_icon_is_optional(user):
    form = TagForm(data={"name": "Backup plan", "colour": TagColour.GREY, "icon": ""}, user=user)
    assert form.is_valid(), form.errors


# ------------------------------------------------------------------ carrying it about


def test_both_travel_in_an_export_and_come_back(user, other_user):
    Tag.objects.create(owner=user, name="Dream job", colour=TagColour.AMBER, icon=TagIcon.STAR)
    document = export_module.build_document(user)
    assert document["tags"][0]["colour"] == "amber"
    assert document["tags"][0]["icon"] == "star"

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))
    restored = Tag.objects.get(owner=other_user, name="Dream job")
    assert (restored.colour, restored.icon) == ("amber", "star")


def imported_from(document, user):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    importer.load(user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())), force=True)


def test_an_older_archive_keeps_what_its_colour_meant(user, other_user):
    """An archive written before #285 holds whatever its owner typed, and there is no reason
    to throw away a perfectly readable "sky"."""
    Tag.objects.create(owner=user, name="Remote")
    document = export_module.build_document(user)
    document["tags"][0]["colour"] = "sky"
    document["tags"][0].pop("icon")

    imported_from(document, other_user)

    assert Tag.objects.get(owner=other_user, name="Remote").colour == "blue"


def test_an_archive_from_a_newer_postulo_does_not_break_this_one(user, other_user):
    Tag.objects.create(owner=user, name="Remote")
    document = export_module.build_document(user)
    document["tags"][0]["icon"] = "rocket"
    document["tags"][0]["colour"] = "chartreuse"

    imported_from(document, other_user)

    restored = Tag.objects.get(owner=other_user, name="Remote")
    assert (restored.colour, restored.icon) == ("grey", "")


# ------------------------------------------------------------------ the rows that were


def test_the_migration_puts_every_row_on_the_palette(user):
    migration = importlib.import_module("postulo.core.migrations.0021_tag_colour_and_icon")
    typed = {
        "Remote": "sky",
        "Dream job": "amber",
        "Backup plan": "slate",
        "Via a friend": "emerald",
        "Relocation": "rose",
        "A hex code": "#f59e0b",
        "Nothing at all": "",
    }
    for name, colour in typed.items():
        made = Tag.objects.create(owner=user, name=name)
        Tag.objects.filter(pk=made.pk).update(colour=colour)

    migration.onto_the_palette(django_apps, None)

    became = dict(Tag.objects.values_list("name", "colour"))
    assert became == {
        "Remote": "blue",
        "Dream job": "amber",
        "Backup plan": "grey",
        "Via a friend": "green",
        "Relocation": "rose",
        "A hex code": "grey",
        "Nothing at all": "grey",
    }
