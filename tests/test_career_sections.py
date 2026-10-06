"""The career page lists its sections down the side, with a count beside each (#175).

Seven sections down one page, each a list with no ceiling, and until this nothing on the
page said what was below the fold: the only way to learn it held Languages was to reach
the bottom. The sidebar is the one Settings uses, generalised to take anchors as well as
pages, so the two cannot drift apart.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from postulo.resume.models import Experience
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db


def the_page(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("resume:overview")).content.decode()


def sidebar_of(html: str) -> str:
    start = html.index('aria-label="Career sections"')
    return html[start : html.index("</nav>", start)]


def test_every_section_is_listed_in_order_with_its_count(client, user):
    Experience.objects.create(
        owner=user, organisation="Aperture", role="Engineer", start_date=dt.date(2020, 1, 1)
    )
    Experience.objects.create(
        owner=user, organisation="Black Mesa", role="Researcher", start_date=dt.date(2022, 1, 1)
    )

    nav = sidebar_of(the_page(client, user))

    hrefs = re.findall(r'href="(#section-[a-z-]+)"', nav)
    assert hrefs == [f"#section-{slug}" for slug in OVERVIEW_ORDER], "one anchor per section"
    counts = re.findall(
        r'data-section-link="section-([a-z-]+)".*?<span class="badge ms-auto"[^>]*>(\d+)</span>',
        nav,
        re.S,
    )
    assert dict(counts)["experience"] == "2"
    assert dict(counts)["language"] == "0", "an empty section is listed, and says so"
    assert len(counts) == len(OVERVIEW_ORDER)


def test_the_anchors_land_on_the_sections(client, user):
    html = the_page(client, user)
    for slug in OVERVIEW_ORDER:
        assert f'<section id="section-{slug}">' in html, slug
        assert str(SECTIONS[slug].plural) in html


def test_nothing_is_current_until_a_browser_says_so(client, user):
    """`aria-current` on an anchor means the section on the screen, which only the browser
    knows; the markup claims nothing, and app.js sets `location` as the page is read."""
    nav = sidebar_of(the_page(client, user))
    assert "aria-current" not in nav
    assert "nav-link-active" not in nav


def test_the_settings_sidebar_still_marks_its_page(client, user):
    """The same template serves Settings, where the current entry is a page."""
    client.force_login(user)
    html = client.get(reverse("settings:appearance")).content.decode()
    start = html.index('aria-label="Settings sections"')
    nav = html[start : html.index("</nav>", start)]
    assert nav.count('aria-current="page"') == 1
    assert "nav-link-active" in nav


def test_the_label_over_the_list_is_the_hook_and_keeps_its_text(client, user):
    """The script swaps the label for the section's title; without it the text stays (#677)."""
    html = the_page(client, user)
    label = re.search(r"<p[^>]*data-section-label[^>]*>(.*?)</p>", html, re.S)
    assert label and label.group(1).strip() == "On this page"
    assert "aria-live" not in label.group(0), "the list's aria-current already says where you are"


def test_a_skill_needs_a_group_and_the_add_link_chooses_one(client, user):
    from postulo.resume.models import Skill, SkillGroup

    client.force_login(user)
    group = SkillGroup.objects.create(owner=user, name="Languages")
    url = reverse("resume:item_create", args=["skill"])

    refused = client.post(url, {"name": "Python"})
    assert refused.status_code == 200
    assert not Skill.objects.filter(owner=user).exists()

    opened = client.get(f"{url}?group={group.pk}")
    assert opened.context["form"].initial["group"] == str(group.pk)
    assert f'value="{group.pk}" selected' in opened.content.decode()

    page = client.get(reverse("resume:overview")).content.decode()
    assert f"{url}?group={group.pk}" in page


def test_a_skill_with_no_group_is_still_on_the_overview(client, user):
    from postulo.resume.models import Skill

    client.force_login(user)
    Skill.objects.create(owner=user, name="Orphaned skill", group=None)
    assert "Orphaned skill" in client.get(reverse("resume:overview")).content.decode()


# ------------------------------------------------------------ the guard for a new section
#
# A section is one entry in the registry and about a dozen other places. This is the list of
# them: a section missing from one fails here, naming the section and the list, instead of
# raising `AttributeError` in a renderer or being skipped on restore (#692).

#: Sections allowed to be missing from a list, as (slug, list name), each with its reason.
#: Empty today: `link` was the one, until the CV-entry map of the importer gained it (#470).
EXCEPTIONS: dict[tuple[str, str], str] = {}


def _missing(slug: str, listname: str, present: bool) -> str | None:
    if present or (slug, listname) in EXCEPTIONS:
        return None
    return f"section {slug!r} is missing from {listname}"


def test_every_section_is_in_every_list_that_must_know_it():
    from postulo.core import export, importer
    from postulo.documents import rendering
    from postulo.resume import candidate, translatable

    block_of = {model: block for block, model in export.RESUME_MODELS.items()}
    importer_models = importer.resume_section_models()
    problems = []
    for slug, spec in SECTIONS.items():
        name = spec.model._meta.model_name
        block = block_of.get(spec.model.__name__)
        checks = {
            "export.TRANSLATION_SECTIONS (also the importer's CV-entry map)": (
                name in export.TRANSLATION_SECTIONS
            ),
            "export.RESUME_MODELS": block is not None,
            "export.RESUME_FIELDS": block in export.RESUME_FIELDS,
            "candidate.KINDS_BY_BLOCK": block in candidate.KINDS_BY_BLOCK,
            "translatable.TRANSLATABLE or TRANSLATES_NOTHING": (
                name in translatable.TRANSLATABLE or name in translatable.TRANSLATES_NOTHING
            ),
            # A single skill is restored and shown inside its group.
            "importer.resume_section_models": block in importer_models or slug == "skill",
        }
        if slug in OVERVIEW_ORDER:
            checks["documents.rendering.SECTION_LABELS"] = name in rendering.SECTION_LABELS
        problems += [m for lst, ok in checks.items() if (m := _missing(slug, lst, ok))]
    assert not problems, "\n".join(problems)


def test_the_two_translation_decisions_do_not_overlap():
    from postulo.resume import translatable

    assert not set(translatable.TRANSLATABLE) & set(translatable.TRANSLATES_NOTHING)
    assert all(translatable.TRANSLATES_NOTHING.values()), "each carries its reason"


def test_the_importer_restores_what_the_archive_writes():
    from postulo.core import export, importer

    restored = importer.resume_section_models()
    assert set(restored) == set(export.RESUME_MODELS) - {"skills"}
    for block, model in restored.items():
        assert model.__name__ == export.RESUME_MODELS[block]


def _one_of_each(user) -> dict:
    """One entry of each section, by slug. A new section adds its line here."""
    from postulo.resume import models as m

    return {
        "experience": lambda: m.Experience.objects.create(
            owner=user, organisation="Aperture", role="Engineer", start_date=dt.date(2020, 1, 1)
        ),
        "education": lambda: m.Education.objects.create(
            owner=user, institution="Universidade", qualification="BSc"
        ),
        "project": lambda: m.Project.objects.create(owner=user, name="Turret firmware"),
        "publication": lambda: m.Publication.objects.create(owner=user, title="On portals"),
        "skill-group": lambda: m.SkillGroup.objects.create(owner=user, name="Languages"),
        "certification": lambda: m.Certification.objects.create(
            owner=user, name="CKA", issuer="CNCF"
        ),
        "honour": lambda: m.Honour.objects.create(
            owner=user, title="Best paper", awarded_by="LSW", awarded_on=dt.date(2022, 9, 15)
        ),
        "membership": lambda: m.Membership.objects.create(
            owner=user, organisation="Engineers", role="Member", start_date=dt.date(2015, 3, 1)
        ),
        "driving-licence": lambda: m.DrivingLicence.objects.create(
            owner=user, country="PT", categories=["B"]
        ),
        "link": lambda: m.Link.objects.create(
            owner=user, title="Portfolio", url="https://alex.example.org"
        ),
        "language": lambda: m.LanguageSkill.objects.create(owner=user, name="French"),
    }


def test_a_cv_holding_one_entry_of_every_section_renders_in_every_format(user):
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents import docx, formats, rendering
    from postulo.documents.models import CV, CVItem, CVKind

    factories = _one_of_each(user)
    cv = CV.objects.create(owner=user, name="Everything", language="en-GB")
    for order, slug in enumerate(OVERVIEW_ORDER):
        assert slug in factories, f"add an entry for section {slug!r} to _one_of_each"
        entry = factories[slug]()
        if slug == "skill-group":
            entry.skills.create(owner=user, name="Python")
        CVItem.objects.create(
            owner=user,
            cv=cv,
            content_type=ContentType.objects.get_for_model(type(entry)),
            object_id=entry.pk,
            order=order,
        )

    for kind in CVKind.values:
        cv.kind = kind
        cv.save(update_fields=["kind"])
        assert rendering.render_cv_html(cv), kind
        outline = rendering.cv_outline(cv)
        assert formats.as_text(outline), kind
        assert docx.write(outline), kind
