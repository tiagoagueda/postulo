"""The page title names the instance first, then the page: *Postulo > Dashboard* (#196).

A tab, a bookmark, the history and a screen reader's announcement all read the title, and
a page named alone cannot be told from the same page on another instance. The instance's
name is put on once, in `base.html`, so a page added later cannot lose it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from postulo.core.models import SiteSettings

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "postulo" / "templates"


def title_of(client, name: str, **kwargs) -> str:
    html = client.get(reverse(name, **kwargs)).content.decode()
    return re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()


def test_a_page_is_named_after_the_instance(client, user):
    # Signed out, the front page is the instance itself and nothing more; signed in, the
    # same address is the dashboard, which is a page like any other.
    assert title_of(client, "core:home") == "Postulo"
    client.force_login(user)
    assert title_of(client, "core:home") == "Postulo &gt; Dashboard"
    assert title_of(client, "accounts:profile") == "Postulo &gt; Your details"
    assert title_of(client, "settings:appearance") == "Postulo &gt; Settings &gt; Appearance"
    assert title_of(client, "core:export") == "Postulo &gt; Settings &gt; Your data"


def test_the_instance_name_is_the_administrators(client, user):
    site = SiteSettings.get()
    site.instance_name = "Acme jobs"
    site.save()

    assert title_of(client, "core:home") == "Acme jobs"
    client.force_login(user)
    assert title_of(client, "accounts:profile") == "Acme jobs &gt; Your details"


def test_a_qualifier_is_a_dot_and_a_level_is_a_chevron(client, user):
    """Two separators, two meanings, so a title never reads as three levels of something."""
    client.force_login(user)
    report = title_of(client, "applications:report")
    assert report.startswith("Postulo &gt; Report · ")

    levels = [
        path
        for path in TEMPLATES.rglob("*.html")
        if re.search(
            r"·\s*\{% translate \"(Settings|Server settings)\" %\}",
            path.read_text(encoding="utf-8"),
        )
    ]
    assert levels == [], "an area is a level, and a level is separated by a chevron"


def test_no_page_puts_the_instance_name_on_by_hand():
    """Once, in base.html. A page that repeated it would say it twice."""
    offenders = [
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if path.name != "base.html"
        and re.search(r"\{% block title %\}.*instance_name", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
