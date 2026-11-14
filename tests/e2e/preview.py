"""Reading a CV or a letter in a browser: the PDF tab is the one that opens (#942)."""

from __future__ import annotations

from playwright.sync_api import Page, expect


def show_quick_view(page: Page) -> None:
    """Open the *Quick view* tab, the HTML frame that follows the page at once. With no script
    there are no tabs and both are already shown."""
    tab = page.get_by_role("tab", name="Quick view")
    if tab.count():
        tab.click()
    expect(page.locator("iframe[data-document-preview]")).to_be_visible()
