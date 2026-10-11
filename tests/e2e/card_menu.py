"""The status menu at a board card's corner (#845), as a test reaches it.

One place for how a card's menu is opened and a row chosen, so the tests that move a card by
its menu say what they mean and a change to the menu is a change here.
"""

from __future__ import annotations

from playwright.sync_api import Locator, expect


def trigger_of(card: Locator) -> Locator:
    return card.locator("[data-card-status-menu] > .dropdown-menu > button[popovertarget]")


def open_menu(card: Locator) -> Locator:
    """Press the pencil; the panel it opens."""
    trigger_of(card).click()
    panel = card.locator("[data-card-status-menu] [data-popover]")
    expect(panel).to_be_visible()
    return panel


def row(card: Locator, status: str) -> Locator:
    return card.locator(f"button[role=menuitemradio][name=status][value={status}]")


def choose_status(card: Locator, status: str) -> None:
    """Open the card's menu and press the row for ``status``."""
    open_menu(card)
    row(card, status).click()
