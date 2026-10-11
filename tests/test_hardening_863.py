"""The date of birth has a calendar beside its boxes, added and not replacing (#863)."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_the_boxes_are_still_there_and_the_calendar_is_added_hidden_until_a_script_runs(
    client, user
):
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    for name in ("birth_day", "birth_month", "birth_year"):
        assert f'name="{name}"' in html
    button = html[html.index("data-birth-picker") - 200 : html.index("data-birth-picker") + 400]
    assert " hidden" in button, "without a script it would fill nothing"
    assert 'popovertarget="birth-picker"' in button
    assert 'type="date"' in html and 'max="' in html and 'min="1900-01-01"' in html


def test_the_script_fills_the_boxes_from_the_picker():
    from pathlib import Path

    js = Path("src/postulo/static/js/app.js").read_text(encoding="utf-8")

    assert "readyBirthPicker" in js and 'box("bday-year").value = parts[1]' in js
