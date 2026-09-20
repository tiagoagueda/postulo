"""What the interface is made of, gathered so a page can show all of it (#292).

A component that is only ever seen inside a feature is a component nobody reviews. The
gallery exists so that a change to the paint can be looked at in one place, in both themes,
before it is spread across 184 templates -- and so that the browser suite has one page where
every component is on screen at once and axe can walk the lot.

The lists here are data rather than markup so that the page stays a page: the swatches, the
button variants and the sizes are what the stylesheet actually defines, written once, and a
template that loops over them cannot fall out of step with itself the way six hand-written
copies did.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Swatch:
    """One colour, named as a template would ask for it."""

    token: str
    #: The utility a template writes, e.g. `bg-ink-500`. Safelisted in `app.css`, because
    #: a class built here is a class the scanner never sees (#292).
    utility: str


@dataclass(frozen=True)
class Scale:
    name: str
    note: str
    swatches: tuple[Swatch, ...]


def _scale(family: str, steps: tuple[int, ...]) -> tuple[Swatch, ...]:
    return tuple(Swatch(token=f"{family}-{step}", utility=f"bg-{family}-{step}") for step in steps)


INK_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
BRAND_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900)

SCALES: tuple[Scale, ...] = (
    Scale(
        name=_("Ink"),
        note=_("The greys. A warm near-black rather than pure black, easier to read at length."),
        swatches=_scale("ink", INK_STEPS),
    ),
    Scale(
        name=_("Brand"),
        note=_("A deep indigo. Ten steps, of which the interface spends a handful."),
        swatches=_scale("brand", BRAND_STEPS),
    ),
)

#: The semantic names a component is painted with: Basecoat's vocabulary, in this palette.
SEMANTIC: tuple[Swatch, ...] = (
    Swatch("background", "bg-background"),
    Swatch("foreground", "bg-foreground"),
    Swatch("card", "bg-card"),
    Swatch("primary", "bg-primary"),
    Swatch("secondary", "bg-secondary"),
    Swatch("muted", "bg-muted"),
    Swatch("accent", "bg-accent"),
    Swatch("destructive", "bg-destructive"),
    Swatch("border", "bg-border"),
    Swatch("input", "bg-input"),
    Swatch("ring", "bg-ring"),
)

#: The tag palette, each tuned to 3:1 against the card in both themes. Six of the seven are
#: unspent until #285 lights them up; the gallery is where they can be seen meanwhile.
TAGS: tuple[str, ...] = ("grey", "blue", "amber", "violet", "teal", "green", "rose")

#: Every way a button is painted, as the attribute a template writes.
BUTTON_VARIANTS: tuple[tuple[str, str], ...] = (
    ("", _("Primary")),
    ("outline", _("Outline")),
    ("ghost", _("Ghost")),
    ("destructive", _("Destructive")),
    ("destructive-ghost", _("Destructive ghost")),
)

BUTTON_SIZES: tuple[tuple[str, str], ...] = (
    ("", _("Default")),
    ("sm", _("Small")),
    ("xs", _("Extra small")),
)

ALERTS: tuple[tuple[str, str], ...] = (
    ("alert-info", _("Something worth knowing.")),
    ("alert-success", _("That worked.")),
    ("alert-warning", _("Worth a second look before you go on.")),
    ("alert-error", _("That did not work, and here is why.")),
)


@dataclass(frozen=True)
class Section:
    """One part of the gallery, with an anchor the sidebar links to."""

    slug: str
    label: str
    entries: tuple = field(default_factory=tuple)


#: The rounding scale, every step derived from `--radius` (#292).
RADII: tuple[tuple[str, str], ...] = (
    ("rounded-sm", "--radius-sm"),
    ("rounded-md", "--radius-md"),
    ("rounded-lg", "--radius-lg"),
    ("rounded-xl", "--radius-xl"),
    ("rounded-full", "—"),
)

#: The three planes. The page is the one without a shadow, which is why it is not a token.
PLANES: tuple[tuple[str, str, str], ...] = (
    ("", _("Page"), _("The background. Everything else is above it.")),
    ("shadow-raised", _("Raised"), _("A card, a table, the masthead once you have scrolled.")),
    ("shadow-floating", _("Floating"), _("A popover or a menu, over the page rather than in it.")),
)

SECTIONS: tuple[Section, ...] = (
    Section("colour", _("Colour")),
    Section("tokens", _("Rounding and depth")),
    Section("type", _("Type")),
    Section("buttons", _("Buttons")),
    Section("fields", _("Fields")),
    Section("surfaces", _("Surfaces")),
    Section("chips", _("Chips and tags")),
    Section("alerts", _("Alerts")),
    Section("empty", _("Nothing there")),
)


def gallery() -> dict:
    """Everything the gallery page draws, in one call."""
    return {
        "scales": SCALES,
        "semantic": SEMANTIC,
        "radii": RADII,
        "planes": PLANES,
        "tags": TAGS,
        "button_variants": BUTTON_VARIANTS,
        "button_sizes": BUTTON_SIZES,
        "alerts": ALERTS,
        "sections": SECTIONS,
    }
