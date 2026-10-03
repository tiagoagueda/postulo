"""Reaching a select's own control from a browser test (#301).

Every `<select>` is still on the page and still the form's control, so a test that only
means *this field holds that value* goes on saying `select_option` on the native select
and `to_have_value` of it: Playwright sets the select and raises its `change`, which is
the event a script, htmx or the stylesheet's `:has()` listens for, and the control beside
it follows.

What a person sees and presses is the button `app.js` builds after the select, and the
list that opens from it. A test about what a person does -- the keys, the pointer, where a
box is on the page, what a screen reader is told -- asks for those here, by the select they
belong to, so that no test spells out how the two are arranged.
"""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

#: The button built for a native select, as a suffix to the select's own selector: its
#: wrapper is the element after the select, and the button is the wrapper's first child.
BUTTON = " + [data-select] > [data-select-trigger]"

#: The same in a page: the button of a select element, or nothing where none was built.
BUTTON_OF = """(select) => {
  const root = select.nextElementSibling;
  return root && root.matches('[data-select]') ? root.querySelector('[data-select-trigger]') : null;
}"""

#: What a page measures where it used to measure a select: the box of its button, which
#: stands exactly where the select stood. The select itself is one pixel, out of sight.
BOX_OF = """(select) => {
  const root = select.nextElementSibling;
  const drawn = root && root.matches('[data-select]')
    ? root.querySelector('[data-select-trigger]') : select;
  const r = drawn.getBoundingClientRect();
  return {x: r.x, y: r.y, left: r.left, top: r.top, right: r.right, bottom: r.bottom,
          width: r.width, height: r.height};
}"""


#: For a script run in a page, the element a person sees for `el`: a select's button where
#: one was built, and `el` itself otherwise -- any other control, and a select on a page
#: whose scripts are off. Written into a script with `.replace("__DRAWN__", DRAWN)`.
DRAWN = """((el) => {
  const root = el && el.tagName === 'SELECT' ? el.nextElementSibling : null;
  return root && root.matches('[data-select]') ? root.querySelector('[data-select-trigger]') : el;
})"""


def button_of(select: Locator) -> Locator:
    """The button a person sees for this select."""
    return select.locator("xpath=following-sibling::*[1][@data-select]").locator(
        "[data-select-trigger]"
    )


def drawn(select: Locator) -> Locator:
    """What a person sees for this select, with scripts on or off: its button where one was
    built, and the select itself where none was."""
    button = button_of(select)
    return button if button.count() else select


def list_of(page: Page, select: Locator) -> Locator:
    """The list that opens from this select's button: its popover, wherever it is drawn."""
    target = button_of(select).get_attribute("popovertarget")
    return page.locator(f'[id="{target}"]')


def options_of(page: Page, select: Locator) -> Locator:
    """The options a person can see in the open list, in order."""
    return list_of(page, select).locator('[role="option"]:not([aria-hidden="true"])')


def open_list(page: Page, select: Locator) -> Locator:
    """Open the list with the pointer and give it back."""
    button = button_of(select)
    button.click()
    panel = list_of(page, select)
    expect(panel).to_be_visible()
    expect(button).to_have_attribute("aria-expanded", "true")
    return panel


def choose(page: Page, select: Locator, label: str) -> None:
    """Choose an option by its words, with the pointer, as a person would."""
    panel = open_list(page, select)
    panel.get_by_role("option", name=label, exact=True).click()
    expect(panel).to_be_hidden()


def active_option(page: Page, select: Locator) -> str:
    """The words of the option the keys are on, as assistive technology is told it: the
    one named by `aria-activedescendant` on whichever control holds the focus."""
    return page.evaluate(
        """() => {
            const holder = document.activeElement;
            const id = holder && holder.getAttribute('aria-activedescendant');
            const option = id ? document.getElementById(id) : null;
            return option ? option.textContent.trim() : '';
        }"""
    )
