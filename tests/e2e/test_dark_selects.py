"""A select and the list it opens are one colour, in every theme (#655).

The closed box was always painted from the ink tokens; the open list was painted only for a
select inside a `.field`, in the browser's own `Canvas`, and left to the platform for every
other: a table's column filter, a board card's status, the country in a group. The list is
the platform's and is in neither the page nor a screenshot, so what is read here is what the
page states about it: the root's `color-scheme`, and the colours the select and each of its
options compute to.

Three states, because the theme has three sources: the profile says dark, nothing is
stamped and the system says dark, and the profile says light over a dark system.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .test_accessibility import furnished  # noqa: F401
from .test_phone_flag import sign_in

pytestmark = pytest.mark.e2e

#: (the profile's theme, the system's, the theme in force)
STATES = [
    pytest.param("dark", "light", "dark", id="profile-dark"),
    pytest.param("system", "dark", "dark", id="system-dark"),
    pytest.param("light", "dark", "light", id="profile-light-over-system-dark"),
]

#: For the first select of each family on the page: how it is found, and what it computes to.
READ = """() => {
  const canvas = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgba = (value) => {
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillStyle = '#000';
    canvas.fillStyle = value;
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
    return {r, g, b, a: a / 255};
  };
  const ground = (element) => {
    for (let at = element; at; at = at.parentElement) {
      const found = rgba(getComputedStyle(at).backgroundColor);
      if (found.a === 1) return found;
    }
    return {r: 255, g: 255, b: 255, a: 1};
  };
  const family = (select) =>
    select.closest('.input-group') ? 'group'
    : select.parentElement.classList.contains('field') ? 'field'
    : 'bare';
  const seen = {};
  for (const select of document.querySelectorAll('select')) {
    const kind = family(select);
    if (seen[kind] || !select.options.length) continue;
    const style = getComputedStyle(select);
    seen[kind] = {
      name: select.name || select.id,
      text: rgba(style.color),
      ground: ground(select),
      options: [...select.querySelectorAll('option, optgroup')].slice(0, 4).map((option) => {
        const own = getComputedStyle(option);
        return {text: rgba(own.color), ground: rgba(own.backgroundColor)};
      }),
    };
  }
  return {
    scheme: getComputedStyle(document.documentElement).colorScheme,
    families: seen,
  };
}"""


def luminance(colour: dict) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour["r"])
        + 0.7152 * channel(colour["g"])
        + 0.0722 * channel(colour["b"])
    )


def contrast(first: dict, second: dict) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(("theme", "system", "in_force"), STATES)
def test_every_select_and_its_list_follow_the_theme(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    theme,
    system,
    in_force,
):
    applicant = furnished["applicant"]
    applicant.profile.theme = theme
    applicant.profile.save(update_fields=["theme"])
    page.emulate_media(color_scheme=system)
    sign_in(page, live_server.url)

    posting = furnished["application"].posting
    pages = ["/accounts/profile/", "/applications/", f"/listings/{posting.pk}/"]
    found: dict[str, dict] = {}
    for path in pages:
        page.goto(f"{live_server.url}{path}")
        read = page.evaluate(READ)
        assert read["scheme"] == in_force, f"{path}: the root says which theme is in force"
        for family, select in read["families"].items():
            found.setdefault(family, {**select, "path": path})

    assert {"field", "bare"} <= set(found), f"a select of each family was visited: {set(found)}"
    for family, select in found.items():
        where = f"{family} select {select['name']!r} on {select['path']}"
        assert contrast(select["text"], select["ground"]) >= 4.5, f"{where}: the closed box"
        assert select["options"], where
        for option in select["options"]:
            assert option["ground"]["a"] == 1, f"{where}: an option has a ground of its own"
            assert contrast(option["text"], option["ground"]) >= 4.5, f"{where}: its list"
            is_dark = luminance(option["ground"]) < 0.18
            assert is_dark == (in_force == "dark"), f"{where}: the list is {in_force}"
