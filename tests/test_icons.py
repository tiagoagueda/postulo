"""The icon tag, and the agreement between the icon list and the committed files."""

import re
from pathlib import Path

import pytest
from django.template import Context, Template, TemplateSyntaxError

from postulo.core.templatetags.postulo import ICON_DIR

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "src" / "postulo" / "templates"
ICON_LIST = REPO / "assets" / "icons.txt"


def render(source: str) -> str:
    return Template("{% load postulo %}" + source).render(Context())


def test_icon_is_inline_svg_sized_by_css_and_hidden_from_assistive_technology():
    html = render('{% icon "sun" %}')
    assert html.startswith("<svg ")
    assert 'data-icon="sun"' in html
    assert 'aria-hidden="true"' in html
    assert 'class="size-4 shrink-0"' in html
    assert 'viewBox="0 0 24 24"' in html
    assert 'stroke="currentColor"' in html
    assert not re.search(r"\s(width|height)=", html)
    assert "<!--" not in html
    assert "lucide" not in html


def test_icon_takes_classes_and_extra_attributes():
    html = render('{% icon "moon" class="size-5 text-brand-600" data_theme_icon="dark" %}')
    assert 'class="size-5 text-brand-600 shrink-0"' in html
    assert 'data-theme-icon="dark"' in html


def test_icon_with_a_label_is_an_image_with_a_name():
    html = render('{% icon "x" label="Close" %}')
    assert 'role="img" aria-label="Close"' in html
    assert "aria-hidden" not in html


def test_attribute_values_are_escaped():
    html = render('{% icon "x" label=\'a"b<c\' %}')
    assert 'aria-label="a&quot;b&lt;c"' in html


def test_unknown_icon_is_a_template_error_that_says_what_to_do():
    with pytest.raises(TemplateSyntaxError, match=re.escape("assets/icons.txt")):
        render('{% icon "no-such-icon" %}')


def test_path_traversal_is_not_an_icon_name():
    with pytest.raises(TemplateSyntaxError):
        render('{% icon "../css/app" %}')


def listed_icons() -> set[str]:
    names = set()
    for line in ICON_LIST.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.add(name)
    return names


def test_committed_icons_are_exactly_the_listed_ones():
    committed = {path.stem for path in ICON_DIR.glob("*.svg")}
    assert committed == listed_icons(), "run `npm run sync:icons` and commit the result"


def test_every_icon_used_in_a_template_is_listed():
    used = set()
    pattern = re.compile(r"{%\s*icon\s+[\"']([a-z0-9-]+)[\"']")
    for path in TEMPLATES.rglob("*.html"):
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    missing = used - listed_icons()
    assert not missing, f"icons used in templates but not in assets/icons.txt: {sorted(missing)}"
