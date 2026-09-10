"""The pieces the dashboard is made of, and the arrangement one person has chosen.

Postulo had two pages answering the same question at two distances. The **dashboard** said
what needs doing today; **Insights** said what the record adds up to. A person had to
remember which page held which number, and neither could be adjusted: the dashboard showed
everybody's six counters whether or not they had ever recorded an interview, and Insights
showed a response funnel to somebody with three applications.

They are one page now, built from widgets. A widget is a small named thing that knows how
to compute itself and which template renders it, registered the way settings sections and
capture sources already are, so the mechanism is one somebody has seen before.

**What is stored is what was chosen, not what was hidden.** That is the opposite of
:mod:`postulo.core.navigation`, and deliberately so. A navigation item added in a later
release should appear for everybody, because the row is a map of the application. A widget
added in a later release should not walk onto a page somebody built. Storing the chosen
keys, in order, gives that, and gives the order for free.

**Every account owns its arrangement from the day the account exists** (#123). It used to
own one only once somebody had touched the setting: before that, ``None`` meant *never
arranged* and the page was computed from the registry. Nothing was shared between accounts
even then -- two people who had never arranged anything were looking at the same *list of
keys*, each computed against their own records -- but the arrangement itself belonged to
nobody, and a grid has to store what a widget was dragged *into*.

**So ``None`` is gone, and what it was load-bearing for is now explicit.** The rule it
carried was that a widget added in a later release reaches somebody who never arranged
anything. With every account holding a list, nobody is ever "never arranged", so that rule
had to be rebuilt rather than dropped -- and the three ways of doing it are not equal. A
*generation marker* ties the answer to a release, which a widget from a plugin installed on
a Tuesday does not have. A *tray* is a way of showing an answer rather than one. So it is a
**seen set**: ``dashboard_known`` holds every key this account has already decided about,
and a key in neither the arrangement nor that set is new *to this account* -- whether it
arrived in a release or with a plugin.

**That is a trade, and it is made knowingly.** A widget added in 0.4.0 no longer appears on
its own for somebody who never arranged their dashboard; it waits on the arrange page, under
*New*, and the dashboard says so. Strictly that is one fewer thing happening without being
asked -- the old behaviour changed somebody's page during an upgrade -- and it is the only
version that also works when the new widget came from a plugin.

**Computing is shared.** Several widgets want the same expensive answer: the funnel, the
response rate and the time-to-reply figures all come out of one pass over the event log.
:class:`Sources` is handed to every widget on a page and works each answer out at most
once, so adding a fourth insights widget costs nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.utils.functional import cached_property

#: The three widths a widget can ask for, and what each spans on a wide screen. The
#: template turns these into classes by name so Tailwind can see them in a template.
WIDTHS = ("quarter", "half", "full")

#: How wide a row is, and what each width takes up in it. Twelve, because a quarter, a half
#: and a whole row all divide into it -- the same twelve the dashboard template draws.
ROW_UNITS = 12
UNITS = {"quarter": 3, "half": 6, "full": 12}

#: The four ways a widget can be moved. Named here so the view, the template and the test
#: all mean the same four (#124).
DIRECTIONS = ("up", "down", "left", "right")


@dataclass(frozen=True)
class Widget:
    """One thing that can appear on the dashboard."""

    key: str
    #: The heading on the page. A widget with no label draws its own.
    label: str
    #: One sentence, in the picker, saying what it is for.
    blurb: str
    #: The partial that renders it, given what ``context`` returned.
    template: str
    #: What it computes, handed the shared :class:`Sources`.
    context: Callable[[Sources], dict]
    width: str = "half"
    #: Which heading it sits under in the picker.
    group: str = ""
    #: In the default arrangement, and where.
    default_order: int | None = None
    #: Who provides it. Empty for Postulo's own; a plugin's name otherwise, and then the key
    #: has to be namespaced with it -- see `register`.
    provider: str = ""

    def __post_init__(self) -> None:
        if self.width not in WIDTHS:
            raise ValueError(f"{self.key}: width must be one of {WIDTHS}")


#: Every widget, keyed and in registration order. Apps fill this from ``AppConfig.ready``.
REGISTRY: dict[str, Widget] = {}


def register(widget: Widget) -> Widget:
    """Add a widget. Registering the same key twice is a mistake, not an override.

    **A bare key belongs to Postulo; anybody else namespaces theirs.** ``counters`` is
    Postulo's, ``acme:counters`` is Acme's, and the two can coexist. Decided now, while it
    is free: a key lands inside every stored arrangement, so a collision discovered after
    people have arranged their dashboards is a data migration of every one of them rather
    than an error at start-up (#123).
    """
    if widget.key in REGISTRY:
        raise ValueError(f"A widget called {widget.key!r} is already registered.")
    prefix, sep, _rest = widget.key.partition(":")
    if widget.provider and (not sep or prefix != widget.provider):
        raise ValueError(
            f"{widget.key!r}: a widget from {widget.provider!r} needs a key beginning "
            f"{widget.provider}:"
        )
    if sep and not widget.provider:
        raise ValueError(f"{widget.key!r}: a namespaced key needs the provider that owns it.")
    REGISTRY[widget.key] = widget
    return widget


def get(key: str) -> Widget | None:
    return REGISTRY.get(key)


def all_widgets() -> list[Widget]:
    return list(REGISTRY.values())


def default_keys() -> list[str]:
    """What somebody sees before they have arranged anything.

    Exactly what the dashboard showed before it was made of widgets, in that order, so an
    upgrade changes nothing for anybody who never opens the setting.
    """
    chosen = [w for w in REGISTRY.values() if w.default_order is not None]
    return [w.key for w in sorted(chosen, key=lambda w: w.default_order)]


def all_keys() -> list[str]:
    return list(REGISTRY)


def is_standard(profile) -> bool:
    """Whether this arrangement is still exactly the standard one.

    What `has_arranged` used to answer from the absence of a value. Asked of the value
    itself now, which is the same question with one fewer state to reason about.
    """
    return keys_for(profile) == default_keys()


def seed(profile) -> None:
    """Give an account its own arrangement, and record what it has been offered.

    Called where a profile is made, and again on the way past for one that somehow has none
    -- an archive restored from before this, a row made by a route nobody thought of.
    Seeding on read as well as on create is what stops "arranged from the day the account
    exists" from depending on every creation path having remembered (#123).
    """
    profile.dashboard_widgets = default_keys()
    # Everything that exists today has been decided about: on the page by the standard
    # arrangement, or off it by the same arrangement. Only what arrives later is new.
    profile.dashboard_known = all_keys()


def keys_for(profile) -> list[str]:
    """The keys this person's dashboard shows, in their order.

    A widget whose key no longer exists -- a plugin uninstalled, a widget dropped in an
    upgrade -- is passed over rather than breaking the page.
    """
    stored = getattr(profile, "dashboard_widgets", None)
    keys = default_keys() if stored is None else list(stored)
    return [key for key in keys if key in REGISTRY]


def known_to(profile) -> set[str]:
    """Every key this account has already decided about.

    Empty means *nothing recorded*, not *nothing decided*. The set only ever grows, and it
    starts as every key there is, so it is never legitimately empty — while an archive
    restored from before it existed, or a row made by a path that missed the seeding, gives
    exactly that. Announcing every widget in Postulo as new to somebody who has been reading
    their own dashboard for a year is the worse of the two mistakes.
    """
    stored = getattr(profile, "dashboard_known", None)
    return set(stored) if stored else set(all_keys())


# ------------------------------------------------------- moving one, in two directions
#
# *Move up* and *move down* are a complete vocabulary for a list and not for a grid, which
# is the whole of #124: a control that works everywhere has to exist before dragging can be
# added on top of it, because dragging reaches only some of the people who use this page.
#
# **The dashboard is a flow rather than a matrix**, and that decides the vocabulary. Widgets
# have widths and fill rows in order, so there is no cell to name -- which rules out a row
# and column picker, and rules out a "move this one, then click a destination" mode that
# would need two interactions and state between them to work without scripts.
#
# So: four directions over the order.
#
# * **left** and **right** move one place, which is what the two buttons already did.
# * **up** and **down** move a whole row, which is the direction a list could not express.
#
# On a narrow screen every widget is one column wide and the two axes coincide. That is not
# a degradation: it is what "up" means when there is only one column, and the same POST
# does it.


def rows_of(keys: list[str]) -> list[list[str]]:
    """The keys grouped as the wide layout draws them.

    A row takes widgets in order until the next one would not fit, exactly as the twelve
    columns in the template fill up. Computed rather than stored, so it stays true when
    somebody changes a widget's width.
    """
    rows: list[list[str]] = []
    used = ROW_UNITS
    for key in keys:
        widget = REGISTRY.get(key)
        width = UNITS.get(widget.width if widget else "half", UNITS["half"])
        if used + width > ROW_UNITS:
            rows.append([])
            used = 0
        rows[-1].append(key)
        used += width
    return rows


def row_of(keys: list[str], key: str) -> int:
    """Which row this key is drawn on, or -1."""
    for number, row in enumerate(rows_of(keys)):
        if key in row:
            return number
    return -1


def move(keys: list[str], key: str, direction: str) -> list[str]:
    """One widget, one step, in one of four directions. Returns the new order.

    Out of range is a no-op rather than a wrap: somebody pressing *up* on the top row means
    to find out that it is the top row, not to send the widget to the bottom.
    """
    keys = list(keys)
    if key not in keys or direction not in ("left", "right", "up", "down"):
        return keys
    index = keys.index(key)

    if direction in ("left", "right"):
        target = index - 1 if direction == "left" else index + 1
        if 0 <= target < len(keys):
            keys[index], keys[target] = keys[target], keys[index]
        return keys

    rows = rows_of(keys)
    here = row_of(keys, key)
    if direction == "up":
        if here <= 0:
            return keys
        # In front of the row above, which is where a widget lands when it moves up a row.
        target = keys.index(rows[here - 1][0])
    else:
        if here < 0 or here >= len(rows) - 1:
            return keys
        # Behind the row below, for the same reason the other way round.
        target = keys.index(rows[here + 1][-1])

    moved = keys.pop(index)
    if index < target:
        target -= 1
    keys.insert(target if direction == "up" else target + 1, moved)
    return keys


def can_move(keys: list[str], key: str, direction: str) -> bool:
    """Whether that direction would change anything. What disables a button."""
    return move(keys, key, direction) != list(keys)


def place(keys: list[str], key: str, position) -> list[str]:
    """Put one widget at a given position in the order. What a drop means.

    The same list the arrows edit, reached a different way: dragging is an addition to the
    control that works everywhere and never a second place to store anything (#125). A
    position outside the list is clamped rather than refused -- a drop past the last row is
    somebody meaning *last*, not somebody making a mistake.
    """
    keys = list(keys)
    if key not in keys:
        return keys
    try:
        wanted = int(position)
    except (TypeError, ValueError):
        return keys
    keys.remove(key)
    keys.insert(max(0, min(len(keys), wanted)), key)
    return keys


def new_for(profile) -> list[Widget]:
    """Widgets this account has never been offered, in registration order.

    Neither on the page nor decided against: a release added one, or somebody installed a
    plugin. They wait here rather than arriving unannounced.
    """
    decided = known_to(profile) | set(keys_for(profile))
    return [widget for key, widget in REGISTRY.items() if key not in decided]


def groups() -> list[tuple[str, list[Widget]]]:
    """The picker's headings, in first-registration order, each with its widgets."""
    out: dict[str, list[Widget]] = {}
    for widget in REGISTRY.values():
        out.setdefault(str(widget.group), []).append(widget)
    return list(out.items())


class Sources:
    """The shared work behind a page of widgets, done at most once each.

    Widgets ask for what they need and do not care who else asked. Without this, a
    dashboard showing the funnel, the response rate and the reply times would walk the
    event log three times to print the same pass three ways.
    """

    def __init__(self, request):
        self.request = request
        self.user = request.user

    @cached_property
    def now(self):
        from django.utils import timezone

        return timezone.now()

    @cached_property
    def applications(self):
        from postulo.applications.models import Application

        return Application.objects.for_user(self.user)

    @cached_property
    def listings(self):
        from postulo.jobs.models import JobPosting

        return JobPosting.objects.for_user(self.user)

    @cached_property
    def insights(self):
        from postulo.applications.analytics import build

        return build(self.user)

    @cached_property
    def quiet(self):
        from postulo.applications.quiet import quiet_applications

        return quiet_applications(self.user, at=self.now)

    @cached_property
    def quiet_after_days(self) -> int:
        from postulo.applications.quiet import threshold_for

        return threshold_for(self.user)


@dataclass
class Rendered:
    """One widget with what it computed, ready for the page to lay out."""

    spec: Widget
    context: dict = field(default_factory=dict)


def build_page(request, profile) -> list[Rendered]:
    """Every widget this person has chosen, in order, each with its own context."""
    sources = Sources(request)
    page = []
    for key in keys_for(profile):
        widget = REGISTRY[key]
        page.append(Rendered(spec=widget, context=widget.context(sources)))
    return page
