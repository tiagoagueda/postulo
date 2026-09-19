"""What the keyboard can reach, read from the markup (#275).

`tests/e2e/test_keyboard_and_focus.py` presses the keys; this holds the shapes that make
those presses work, so a template written tomorrow fails here rather than in a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

TEMPLATES = sorted((Path(__file__).resolve().parents[1] / "src" / "postulo").rglob("*.html"))

#: The one scroll box that is a row of links rather than a table: every child is a tab
#: stop already, so the browser scrolls it as they are reached, and a stop of its own would
#: be one more before every settings page.
NAVIGATION_STRIP = "sidebar.html"


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_every_scroll_box_is_a_named_stop_in_the_tab_order(path: Path):
    """`.scroll-x` scrolls instead of the page (#113) and had no way in: once a table
    overflowed, a plain-text cell off the edge could never be brought into view from the
    keyboard. A box is `<c-scroll-box label="…">`, or carries the same three attributes."""
    text = path.read_text(encoding="utf-8")
    if path.name == NAVIGATION_STRIP or path.name == "scroll_box.html":
        return
    for match in re.finditer(r"<(\w+)[^>]*\bscroll-x\b[^>]*>", text):
        tag = match.group(0)
        # A command block is a group rather than a region: two on one page would be two
        # landmarks with one name, which axe refuses, and a landmark is more than it is.
        named = 'role="region"' in tag or 'role="group"' in tag
        assert 'tabindex="0"' in tag and named and "aria-label=" in tag, (
            f"{path.name}: a scroll box a keyboard cannot reach: {tag[:80]}"
        )


def test_the_component_is_what_the_boxes_are_made_of():
    assert sum(t.read_text(encoding="utf-8").count("<c-scroll-box") for t in TEMPLATES) >= 15


@pytest.mark.django_db
def test_a_scroll_box_renders_as_a_named_region(client, user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=user, name="Aperture Science")  # an empty list has no table
    client.force_login(user)
    html = client.get(reverse("jobs:company_list")).content.decode()
    assert re.search(
        r'<div class="scroll-x card p-0" tabindex="0" role="region" aria-label="Companies"', html
    )


@pytest.mark.django_db
def test_the_current_shape_is_pressed_not_unavailable(client, user):
    """The current shape's button was `disabled`: out of the tab order, at half strength,
    reading as *unavailable* while announcing *current* (#275)."""
    client.force_login(user)
    html = client.get(reverse("applications:list")).content.decode()
    switch = html[
        html.index("data-shape-switch") : html.index("</div>", html.index("data-shape-switch"))
    ]
    assert 'aria-pressed="true"' in switch and 'aria-pressed="false"' in switch
    assert "disabled" not in switch and "aria-current" not in switch


@pytest.mark.django_db
def test_the_failure_alert_can_be_put_away(client, user):
    """The region is a paragraph that stays empty until there is something to say; the
    dismiss button sits beside it, outside the region, so the announcement is only ever the
    words, and the script clears them on the button and on Escape (#275)."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    alert = html[
        html.index('class="page-alert"') : html.index("</div>", html.index('class="page-alert"'))
    ]
    assert '<p role="alert" data-htmx-alert' in alert
    assert "data-alert-close" in alert and 'aria-label="Dismiss"' in alert

    script = (Path(__file__).resolve().parents[1] / "src/postulo/static/js/app.js").read_text(
        "utf-8"
    )
    assert '"[data-htmx-alert]"' in script
    assert "[data-alert-close]" in script


def test_the_stylesheet_hides_the_alert_by_its_words_and_makes_room_for_it():
    css = (Path(__file__).resolve().parents[1] / "assets/css/app.css").read_text("utf-8")
    assert '.page-alert:has(> [role="alert"]:empty)' in css
    assert "scroll-padding-bottom" in css
    assert "before:w-px before:bg-ink-300" in css, "the resize handle can be seen at rest"
