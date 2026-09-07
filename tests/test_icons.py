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


# ------------------------------------------------- geometry the tag must not eat

#: Icons whose shape depends on width and height on a child element rather than on a
#: path. These are the ones that vanish, wholly or partly, if the tag strips those
#: attributes everywhere instead of only from the root.
ICONS_WITH_SIZED_CHILDREN = ("briefcase", "calendar", "layout-dashboard", "mail", "monitor")


@pytest.mark.parametrize("name", ICONS_WITH_SIZED_CHILDREN)
def test_a_child_element_keeps_the_size_that_gives_it_a_shape(name):
    """The root's fixed 24 pixels must go; a rect's own dimensions must not.

    Reported as "the email icon shows only a V": Lucide draws an envelope as a flap path
    plus a rect, the rect's width and height were being stripped along with the root's,
    and a rect with no dimensions draws nothing at all. Five icons were affected and had
    been since the tag was written — three of them in the settings sidebar.
    """
    markup = render(f'{{% icon "{name}" %}}')
    body = markup[markup.index(">") + 1 :]

    assert re.search(r'(?<![-\w])width="', body) and re.search(r'(?<![-\w])height="', body), (
        f"{name}: a child element lost the size that gives it a shape"
    )


@pytest.mark.parametrize("name", ICONS_WITH_SIZED_CHILDREN)
def test_the_root_still_loses_its_fixed_size_and_class(name):
    """The other half: one file has to serve a 16 pixel glyph and a 48 pixel one."""
    markup = render(f'{{% icon "{name}" %}}')
    root = markup[: markup.index(">") + 1]

    # Not a substring test: `stroke-width="2"` contains "width=" and must survive.
    assert not re.search(r'(?<![-\w])(width|height)="', root), f"{name}: root keeps a fixed size"
    assert "lucide" not in root, f"{name}: root keeps Lucide's own class"
    assert 'viewBox="0 0 24 24"' in root, f"{name}: the viewBox is the geometry; it stays"


def test_the_mail_icon_draws_both_the_flap_and_the_envelope():
    """The reported symptom, stated as itself."""
    markup = render('{% icon "mail" %}')

    assert "<path" in markup, "the flap"
    assert re.search(r'<rect[^>]*width="20"[^>]*height="16"', markup), "the envelope"
