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

**A plugin's widgets join Postulo's (#1102).** A plugin declares a `DashboardWidget` on the
plugin surface, and it is here under ``plugin:key``, beside the ones registered above, for
as long as the plugin is installed. Each is offered only to somebody the instance's plugin
policy lets use the plugin, and drawn the way Postulo's are -- once per view, nothing kept
between views -- with three differences: it is handed the person and their connection
rather than :class:`Sources`, Postulo draws its card and heading, and what its template
writes is held to an allowlist (`sanitise`). One that raises is left off and logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import nh3
from django.utils.functional import cached_property
from django.utils.safestring import mark_safe

from postulo.plugins.base import WIDGET_WIDTHS

logger = logging.getLogger(__name__)

#: The three widths a widget can ask for, and what each spans on a wide screen. The
#: template turns these into classes by name so Tailwind can see them in a template. A
#: plugin's widget asks in the same words, so the list is the plugin surface's.
WIDTHS = WIDGET_WIDTHS

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
    #: What it is called wherever it is *referred to* rather than headed: the arrange page, the
    #: buttons that move it, the line saying a new one is waiting. The label, when there is one.
    #: A widget that draws its own heading -- or none -- says its name here, or the arrange
    #: page reads "Take  off" and a screen reader hears four arrows that do not say what they
    #: move (#165).
    name: str = ""

    def __post_init__(self) -> None:
        if self.width not in WIDTHS:
            raise ValueError(f"{self.key}: width must be one of {WIDTHS}")

    @property
    def called(self) -> str:
        """The widget's name in words. What every sentence *about* a widget says."""
        return str(self.name or self.label)


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
    _check(widget)
    REGISTRY[widget.key] = widget
    return widget


def _check(widget: Widget) -> None:
    """The rules every widget's key and name follow, Postulo's and a plugin's alike."""
    prefix, sep, _rest = widget.key.partition(":")
    if widget.provider and (not sep or prefix != widget.provider):
        raise ValueError(
            f"{widget.key!r}: a widget from {widget.provider!r} needs a key beginning "
            f"{widget.provider}:"
        )
    if sep and not widget.provider:
        raise ValueError(f"{widget.key!r}: a namespaced key needs the provider that owns it.")
    if not (widget.name or widget.label):
        # Refused here rather than papered over with the key: a key is a code, and the arrange
        # page, its buttons and a screen reader all need words (#165).
        raise ValueError(
            f"{widget.key!r}: a widget needs a label, or a name when it draws its own heading."
        )


# ------------------------------------------------------------- the plugins' widgets


@dataclass(frozen=True)
class FromPlugin:
    """What a widget a plugin brought needs besides the `Widget` itself."""

    plugin: str
    kind: str
    #: The plugin's own `DashboardWidget`.
    declared: object


#: The registry's generation the widgets were last read at, and what they came to. See
#: `_from_plugins`.
_merged: tuple[int, dict[str, Widget], dict[str, FromPlugin]] | None = None


def _installed_plugins() -> list[tuple[str, object]]:
    """Every plugin loaded now, with its kind, in the registry's order."""
    from postulo.plugins import registry as plugin_registry

    found: list[tuple[str, object]] = []
    for kind in plugin_registry.GROUPS:
        try:
            found.extend((kind, plugin) for plugin in plugin_registry.plugins(kind))
        except Exception:  # pragma: no cover - a kind that cannot even be enumerated
            logger.exception("The %s plugins could not be read for their widgets", kind)
    return found


def _from_plugins() -> tuple[dict[str, Widget], dict[str, FromPlugin]]:
    """The widgets every installed plugin declares, as `Widget`s keyed ``plugin:key``.

    Read again only when `plugins.registry` may have loaded them again -- an install, a
    removal, a switch, here or in another process -- which its `generation` says in one look
    at the record. The page asks this for every widget it lays out and every arrow it
    offers, so asking the registry for every kind each time is what it must not do. A
    declaration that cannot be used is logged when it is read and costs that widget alone.
    """
    global _merged

    from postulo.plugins import registry as plugin_registry
    from postulo.plugins.base import label_of, widget_problems, widgets_of

    if _merged is not None and _merged[0] == plugin_registry.generation():
        return _merged[1], _merged[2]
    installed = _installed_plugins()

    found: dict[str, Widget] = {}
    origins: dict[str, FromPlugin] = {}
    for kind, plugin in installed:
        name = str(getattr(plugin, "name", "") or "")
        try:
            declared = widgets_of(plugin)
        except Exception:
            # Somebody else's list, read on somebody's page: one that will not be read costs
            # its own widgets and nobody else's.
            logger.exception("Plugin %r's dashboard widgets could not be read", name)
            continue
        for one in declared:
            problems = widget_problems(one) if name else ["the plugin has no name"]
            if problems:
                logger.warning(
                    "Plugin %r offered a dashboard widget that cannot be used: %s",
                    name,
                    "; ".join(problems),
                )
                continue
            key = f"{name}:{one.key}"
            if key in found or key in REGISTRY:
                logger.warning("Dashboard widget %r was offered twice; the first keeps it", key)
                continue
            widget = Widget(
                key=key,
                label=one.label,
                blurb=one.blurb,
                template="widgets/plugin.html",
                context=_plugin_context(key),
                width=one.width,
                group=label_of(plugin),
                provider=name,
            )
            try:
                _check(widget)
            except ValueError as refused:
                logger.warning("Plugin %r: %s", name, refused)
                continue
            found[key] = widget
            origins[key] = FromPlugin(plugin=name, kind=kind, declared=one)
    # Read after loading, which may itself have moved it: the next look then matches.
    _merged = (plugin_registry.generation(), found, origins)
    return found, origins


def _allowed(plugin: str, user) -> bool:
    """Whether the instance's plugin policy lets this person use this plugin."""
    from postulo.plugins.policy import decide

    return user is not None and decide(plugin, user).on


def registry(user=None) -> dict[str, Widget]:
    """Every widget there is: Postulo's, then the plugins', keyed and in that order.

    Given a person, only the plugins' widgets the instance's policy lets that person use: one
    switched off for them is, for them, a plugin that is not installed -- offered nowhere,
    drawn nowhere, and passed over in their arrangement as an uninstalled one is.
    """
    plugin_widgets, origins = _from_plugins()
    if not plugin_widgets:
        return dict(REGISTRY)
    if user is not None:
        plugin_widgets = {
            key: widget
            for key, widget in plugin_widgets.items()
            if _allowed(origins[key].plugin, user)
        }
    return {**REGISTRY, **plugin_widgets}


def _for(profile) -> dict[str, Widget]:
    """`registry` as this profile's person sees it, reading the account only when a plugin
    has a widget to be filtered."""
    if not _from_plugins()[0]:
        return dict(REGISTRY)
    return registry(getattr(profile, "user", None) if profile is not None else None)


def get(key: str, user=None) -> Widget | None:
    return registry(user).get(key)


def all_widgets(user=None) -> list[Widget]:
    return list(registry(user).values())


def default_keys() -> list[str]:
    """What somebody sees before they have arranged anything.

    Exactly what the dashboard showed before it was made of widgets, in that order, so an
    upgrade changes nothing for anybody who never opens the setting.
    """
    chosen = [w for w in REGISTRY.values() if w.default_order is not None]
    return [w.key for w in sorted(chosen, key=lambda w: w.default_order)]


def all_keys() -> list[str]:
    return list(registry())


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
    upgrade -- is passed over rather than breaking the page. So is a plugin's widget the
    instance's policy does not let this person use (#1102).
    """
    stored = getattr(profile, "dashboard_widgets", None)
    keys = default_keys() if stored is None else list(stored)
    known = _for(profile)
    return [key for key in keys if key in known]


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
    known = registry()
    for key in keys:
        widget = known.get(key)
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
    return [widget for key, widget in _for(profile).items() if key not in decided]


def groups(user=None) -> list[tuple[str, list[Widget]]]:
    """The picker's headings, in first-registration order, each with its widgets.

    A plugin's widgets come under the plugin's name, after Postulo's own headings.
    """
    return _grouped(registry(user))


def groups_for(profile) -> list[tuple[str, list[Widget]]]:
    """`groups`, as this profile's person is offered them."""
    return _grouped(_for(profile))


def _grouped(known: dict[str, Widget]) -> list[tuple[str, list[Widget]]]:
    out: dict[str, list[Widget]] = {}
    for widget in known.values():
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

        # Each as this person stands with it (#896): their own status and applications.
        return JobPosting.objects.visible_to(self.user).with_status(self.user)

    @cached_property
    def insights(self):
        # `insights_for` rather than `build`: the figures are the whole history reduced, and
        # nothing about a search changes between two page loads (#231).
        from postulo.applications.analytics import insights_for

        return insights_for(self.user)

    @cached_property
    def quiet(self):
        from postulo.applications.quiet import quiet_applications

        return quiet_applications(self.user, at=self.now)

    @cached_property
    def quiet_after_days(self) -> int:
        from postulo.applications.quiet import threshold_for

        return threshold_for(self.user)

    @cached_property
    def quiet_varies(self) -> bool:
        """Whether some applications have a number of days of their own (#1099)."""
        from postulo.applications.quiet import rule_for

        return rule_for(self.user).varies


@dataclass
class Rendered:
    """One widget with what it computed, ready for the page to lay out."""

    spec: Widget
    context: dict = field(default_factory=dict)
    #: While the page is being arranged (#201): which of the four directions would change
    #: anything, so a button that cannot act says so before it is pressed (#124). Empty on
    #: the page as read.
    moves: dict = field(default_factory=dict)
    #: A plugin's widget that could not be drawn (#1102). Kept as a cell with its controls
    #: and no body while the page is being arranged, so it can still be taken off.
    left_out: bool = False


def build_page(request, profile, *, keep_left_out: bool = False) -> list[Rendered]:
    """Every widget this person has chosen, in order, each with its own context.

    A plugin's widget that could not be drawn is not on the page, or is an empty cell with
    ``keep_left_out``, which is what the arrange mode asks for.
    """
    sources = Sources(request)
    known = _for(profile)
    page = []
    for key in keys_for(profile):
        widget = known[key]
        context = widget.context(sources)
        if context is None:
            if keep_left_out:
                page.append(Rendered(spec=widget, left_out=True))
            continue
        page.append(Rendered(spec=widget, context=context))
    return page


# ------------------------------------------------------- drawing a plugin's widget
#
# A plugin's template is the plugin's code, and runs because an administrator installed the
# plugin -- the trust a document theme has (`plugins.themes`). What it *writes* lands on
# Postulo's own page, beside the person's records and under Postulo's script, so it keeps the
# rule every other piece of a plugin's markup keeps: a plugin never puts unescaped HTML in
# front of a person. A form is Postulo's to draw (`FieldSpec`), a remote item is escaped text
# (`RemoteItem`), an attribute on a card is escaped and named from a short list
# (`form_attributes`), and a widget's body goes through an allowlist, whatever ``|safe`` or
# ``{% autoescape off %}`` let through in its template.

#: What a widget's body may hold: words, lists, tables, the lower headings (the card's own
#: is an ``h2``) and links. No script, style, form, image, frame or ``svg``.
ALLOWED_TAGS = frozenset(
    {
        "a", "abbr", "b", "bdi", "blockquote", "br", "caption", "code", "data", "dd", "div",
        "dl", "dt", "em", "figcaption", "figure", "h3", "h4", "h5", "h6", "hr", "i", "li",
        "mark", "ol", "p", "q", "s", "small", "span", "strong", "sub", "sup", "table",
        "tbody", "td", "tfoot", "th", "thead", "time", "tr", "ul",
    }
)  # fmt: skip
#: And what each may carry. No ``id`` (the page's own would collide), no ``data-`` (Postulo's
#: script acts on those), no ``style`` and no event handler.
ALLOWED_ATTRIBUTES = {
    "*": {"class", "title", "lang", "dir", "role", "aria-label", "aria-hidden"},
    "a": {"href", "hreflang"},
    "data": {"value"},
    "li": {"value"},
    "ol": {"start", "reversed"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope", "abbr"},
    "time": {"datetime"},
}
ALLOWED_SCHEMES = frozenset({"http", "https", "mailto"})


def sanitise(html: str) -> str:
    """A plugin widget's body, held to the allowlist above.

    A path with no scheme is kept: a widget pointing at one of the person's applications
    points at a page of this instance.
    """
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        clean_content_tags={"script", "style"},
        attributes={tag: set(names) for tag, names in ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(ALLOWED_SCHEMES),
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


@dataclass(frozen=True)
class Part:
    """A plugin widget's body for one connection: the connection's name, and the markup."""

    label: str
    html: str


def _connections_for(origin: FromPlugin, user) -> list:
    """What the widget is drawn for: each of the person's enabled connections to the plugin
    that the policy lets act, as the Connections page orders them, or ``[None]``."""
    from postulo.plugins.base import CONNECTED_KINDS
    from postulo.plugins.models import Connection
    from postulo.plugins.policy import allows

    if origin.kind not in CONNECTED_KINDS:
        return [None]
    held = [
        connection
        for connection in Connection.objects.for_user(user)
        .enabled()
        .filter(kind=origin.kind, plugin=origin.plugin)
        .select_related("owner")
        if allows(connection)
    ]
    return held or [None]


def draw(declared, user, connection) -> str:
    """One plugin widget's body for one connection: asked for, rendered and sanitised.

    Raises whatever the plugin raised, and `TypeError` for a context that is not a dict.
    `build_page` is what leaves a widget out for it; a plugin's own tests reach this through
    `postulo.plugins.testing.draw_widget` and see the error.
    """
    from django.template.loader import get_template

    context = declared.context(user, connection)
    if not isinstance(context, Mapping):
        raise TypeError(f"{declared.key!r} returned {type(context).__name__}, not a dict")
    return sanitise(get_template(declared.template).render(dict(context)))


def _plugin_context(key: str) -> Callable[[Sources], dict | None]:
    """The `Widget.context` of a plugin's widget: its parts, or ``None`` to leave it out."""

    def context(sources: Sources) -> dict | None:
        origin = _from_plugins()[1].get(key)
        user = sources.user
        if origin is None or not _allowed(origin.plugin, user):
            return None
        parts = []
        try:
            for connection in _connections_for(origin, user):
                html = mark_safe(draw(origin.declared, user, connection))  # noqa: S308 -- the allowlist
                parts.append(Part(label=str(connection or ""), html=html))
        except Exception:
            # Somebody else's code on somebody's dashboard: it costs its own cell and
            # nothing else on the page.
            logger.exception("Dashboard widget %r could not be drawn", key)
            return None
        return {"parts": parts, "several": len(parts) > 1}

    return context
