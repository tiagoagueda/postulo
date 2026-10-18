"""The calendar of Postulo's own, `<c-date-picker>`, and the row it sits in (#928)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def profile_html(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("accounts:profile")).content.decode()


def test_the_component_draws_the_frame_and_its_labels_and_no_days(client, user):
    html = profile_html(client, user)

    panel = html[html.index('id="birth-picker"') :]
    panel = panel[: panel.index("</div>\n</div>") if "</div>\n</div>" in panel else 3000]
    assert (
        "popover"
        in html[html.index('id="birth-picker"') - 5 : html.index('id="birth-picker"') + 120]
    )
    assert 'role="dialog"' in panel and 'aria-label="Date of birth"' in panel
    for marker in (
        "data-date-picker-previous",
        "data-date-picker-next",
        "data-date-picker-month",
        "data-date-picker-year",
        "data-date-picker-grid",
    ):
        assert marker in panel
    for label in ("Previous month", "Next month", "Month", "Year"):
        assert f'aria-label="{label}"' in panel
    assert "It fills the day, month and year boxes." in panel
    # The days and the names of months and weekdays are the browser's, drawn by app.js.
    assert "January" not in panel and '<button type="button" data-date' not in panel


def test_the_two_selects_are_never_posted_with_the_form(client, user):
    html = profile_html(client, user)

    tags = re.findall(r"<select[^>]*data-date-picker-(?:month|year)[^>]*>", html)
    assert len(tags) == 2
    assert all(" name=" not in tag and "data-native" in tag for tag in tags)


def test_the_boxes_and_the_button_share_one_row_that_does_not_wrap(client, user):
    html = profile_html(client, user)

    row = html[html.index("<legend") :]
    row = row[row.index("<div class=") : row.index('id="birth-picker"')]
    opening = row[: row.index(">") + 1]
    assert "flex-wrap" not in opening and "flex " in opening
    assert row.count("data-birth-picker") >= 1
    assert "shrink-0" in row[row.index("data-birth-picker") - 400 : row.index("data-birth-picker")]


def test_the_script_follows_the_date_picker_dialog_pattern():
    from pathlib import Path

    js = Path("src/postulo/static/js/app.js").read_text(encoding="utf-8")

    for key in (
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Home",
        "End",
        "PageUp",
        "PageDown",
    ):
        assert f'case "{key}"' in js
    assert 'aria-current", "date"' in js and 'aria-pressed", "true"' in js
    assert "Intl.DateTimeFormat" in js
