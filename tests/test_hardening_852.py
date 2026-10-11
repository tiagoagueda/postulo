"""A setting for how buttons with an icon and words read (#852)."""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.urls import reverse

from postulo.core import export, importer
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


def body_tag(client, url) -> str:
    html = client.get(url).content.decode()
    return re.search(r"<body[^>]*>", html)[0]


def test_both_is_the_default_and_stamps_nothing(client, user):
    client.force_login(user)

    assert "data-buttons" not in body_tag(client, reverse("settings:appearance"))


@pytest.mark.parametrize("choice", ["icons", "labels"])
def test_a_choice_is_stamped_on_the_body(client, user, choice):
    user.profile.button_labels = choice
    user.profile.save()
    client.force_login(user)

    assert f'data-buttons="{choice}"' in body_tag(client, reverse("settings:appearance"))


def test_the_company_group_draws_its_words_in_a_label_span(client, user):
    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    html = client.get(company.get_absolute_url()).content.decode()

    group = html[html.index("data-company-actions") :].split("</div>")[0]
    assert group.count('class="btn-label"') == 4
    assert "sr-only sm:not-sr-only" not in group


def test_the_stylesheet_hides_the_words_or_the_icon_as_chosen():
    from pathlib import Path

    css = Path("src/postulo/static/css/app.css").read_text(encoding="utf-8")

    assert 'body[data-buttons="icons"] .btn-label' in css
    assert 'body[data-buttons="labels"] :has(> .btn-label) > svg[data-icon]' in css
    assert 'body:not([data-buttons="labels"]) [role="group"] .btn-label' in css


def test_the_setting_is_on_the_appearance_page_and_is_saved(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    assert 'name="button_labels"' in page

    client.post(
        reverse("settings:appearance"),
        {
            "theme": "system",
            "density": "comfortable",
            "identifier_style": "both",
            "button_labels": "icons",
            "quiet_after_days": 14,
            "closing_notice_days": 3,
            "navigation": [],
        },
    )
    user.profile.refresh_from_db()
    assert user.profile.button_labels == "icons"


def test_a_page_posted_without_it_leaves_it_as_it_was(client, user):
    user.profile.button_labels = "labels"
    user.profile.save()
    client.force_login(user)

    client.post(
        reverse("settings:appearance"),
        {
            "theme": "system",
            "density": "comfortable",
            "quiet_after_days": 14,
            "closing_notice_days": 3,
            "navigation": [],
        },
    )

    user.profile.refresh_from_db()
    assert user.profile.button_labels == "labels"


def test_the_setting_travels_in_the_archive_and_a_bad_value_is_refused(user, other_user):
    user.profile.button_labels = "icons"
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    assert document["account"]["profile"]["button_labels"] == "icons"

    def load(value):
        document["account"]["profile"]["button_labels"] = value
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(export.MANIFEST_NAME, json.dumps(document))
        buffer.seek(0)
        importer.load(other_user, zipfile.ZipFile(buffer))
        other_user.profile.refresh_from_db()
        return other_user.profile.button_labels

    assert load("icons") == "icons"
    assert load("sparkles") == "icons", "a value the column refuses is left out"
