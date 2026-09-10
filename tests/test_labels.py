"""One control for choosing several labels, in two places, layered over what already works.

> the selection should be in the form of labels, not tick boxes

Industries were a row of checkboxes; tags were Django's default scrolling box you
ctrl-click. Neither is a label, and nothing in Postulo drew a chip — so this is a new
control rather than a restyling of an old one (#139).

**The rule the control obeys is the one the board's dragging obeys**: an addition to the
control that works everywhere, never a replacement for it. Everything here is about that
being true — the native controls are still in the page, still submitting, and a form
posted with no script at all does exactly what it did before. The chips themselves need a
browser to be tested honestly, and `tests/e2e/test_labels.py` is where they are.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.core.models import Tag

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------- the control that stays


def test_the_company_form_still_posts_checkboxes(client, user):
    """With no script the industries field is what it always was."""
    client.force_login(user)

    html = client.get(reverse("jobs:company_create")).content.decode()

    assert 'type="checkbox"' in html or "Your list is empty" in html
    assert 'name="new_industries"' in html
    assert "data-labels-existing" in html


def test_the_application_form_still_posts_a_select(client, user):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    client.force_login(user)

    html = client.get(reverse("applications:update", args=[application.pk])).content.decode()

    assert 'name="tags"' in html
    assert 'name="new_tags"' in html
    assert "data-labels-existing" in html


def test_both_places_take_their_wrapper_from_one_file():
    """Written once, or the two drift and only one of them keeps working."""
    from pathlib import Path

    for name in ("jobs/company_form.html", "partials/tags_field.html"):
        source = Path(f"src/postulo/templates/{name}").read_text(encoding="utf-8")
        assert "partials/label_attrs.html" in source


def test_the_words_the_script_needs_come_from_the_catalogue(client, user):
    """A string built in JavaScript is a string no catalogue has."""
    client.force_login(user)

    html = client.get(reverse("jobs:company_create")).content.decode()

    for attribute in ("data-labels-remove", "data-labels-added", "data-labels-removed"):
        assert attribute in html
    assert "{label}" in html, "the name goes inside the sentence, not beside it"


# ----------------------------------------------------- a name that does not exist yet


def test_a_typed_tag_that_is_new_is_made(client, user):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    client.force_login(user)

    client.post(
        reverse("applications:update", args=[application.pk]),
        {
            "status": Status.DRAFT,
            "channel": "",
            "priority": 2,
            "deadline": "",
            "contact": "",
            "new_tags": "Remote, Deadline soon",
        },
    )

    assert sorted(tag.name for tag in application.tags.all()) == ["Deadline soon", "Remote"]


def test_two_spellings_of_a_new_tag_are_one_tag(user):
    Tag.named(user, ["Remote", "remote", "REMOTE"])

    assert Tag.objects.for_user(user).count() == 1


def test_a_tag_that_already_exists_keeps_its_colour(user):
    Tag.objects.create(owner=user, name="Remote", colour="amber")

    found = Tag.named(user, ["remote"])

    assert found[0].colour == "amber", "matched by slug, and not re-made"
    assert found[0].name == "Remote", "and it keeps the spelling it was first given"


def test_a_new_tag_has_no_colour_of_its_own(user):
    """Colours are chosen on the tags page, where there is room to see them together."""
    assert Tag.named(user, ["Remote"])[0].colour == ""


def test_typed_and_ticked_tags_are_both_kept(user):
    from postulo.applications.forms import ApplicationDetailsForm

    already = Tag.objects.create(owner=user, name="Remote")
    form = ApplicationDetailsForm(user=user, data={})
    form.cleaned_data = {"tags": [already], "new_tags": "Deadline soon"}

    chosen = form.chosen_tags()

    assert [tag.name for tag in chosen] == ["Remote", "Deadline soon"]


def test_a_tag_typed_that_was_also_ticked_is_not_doubled(user):
    from postulo.applications.forms import ApplicationDetailsForm

    already = Tag.objects.create(owner=user, name="Remote")
    form = ApplicationDetailsForm(user=user, data={})
    form.cleaned_data = {"tags": [already], "new_tags": "remote"}

    assert form.chosen_tags() == [already]


def test_nobody_elses_tag_is_reachable(user, other_user):
    Tag.objects.create(owner=other_user, name="Remote")

    mine = Tag.named(user, ["Remote"])[0]

    assert mine.owner == user
    assert Tag.objects.filter(name="Remote").count() == 2, "two people, two tags"


# ------------------------------------------------- what the control must not become


def test_the_chip_is_a_target_of_the_size_the_guidelines_ask_for():
    """SC 2.5.8 has caught this project twice — at 19 pixels on the plugins page, and on
    the telephone rows. A × in the corner of a pill is exactly the shape that fails.
    """
    from pathlib import Path

    css = Path("assets/css/app.css").read_text(encoding="utf-8")
    chip = css.split(".chip-remove {")[1].split("}")[0]

    assert "h-6 w-6" in chip, "24 by 24"


def test_nothing_names_a_side_of_the_page():
    """The × sits at the end edge, not the right. The template lint covers markup; this
    covers the stylesheet the chips are drawn with.
    """
    import re
    from pathlib import Path

    css = Path("assets/css/app.css").read_text(encoding="utf-8")
    chips = "".join(
        css.split(f".{name} {{")[1].split("}")[0] for name in ("chip", "chip-new", "chip-remove")
    )

    assert not re.search(r"\b(pl|pr|ml|mr)-", chips)
    assert "ps-3" in chips and "pe-0.5" in chips


def test_the_script_does_not_replace_the_control_it_is_layered_over():
    """`app.js` states the rule; this is what holds the labels code to it."""
    from pathlib import Path

    source = Path("src/postulo/static/js/app.js").read_text(encoding="utf-8")
    block = source.split("labelSources")[0].split("/* ---")[-1]

    assert "hidden = true" in source, "the native control is hidden, not removed"
    assert "remove()" not in block
