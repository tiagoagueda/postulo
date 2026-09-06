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
added in a later release should appear for somebody who has never arranged their dashboard,
and stay away from somebody who has built a page deliberately. Storing the chosen keys, in
order, gives both, and gives the order for free.

Nothing stored (``None``) and nothing chosen (``[]``) are different answers, and keeping
them apart is the difference between clearing your dashboard and being handed the defaults
back for your trouble.

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

    def __post_init__(self) -> None:
        if self.width not in WIDTHS:
            raise ValueError(f"{self.key}: width must be one of {WIDTHS}")


#: Every widget, keyed and in registration order. Apps fill this from ``AppConfig.ready``.
REGISTRY: dict[str, Widget] = {}


def register(widget: Widget) -> Widget:
    """Add a widget. Registering the same key twice is a mistake, not an override."""
    if widget.key in REGISTRY:
        raise ValueError(f"A widget called {widget.key!r} is already registered.")
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


def has_arranged(profile) -> bool:
    """Whether this person has ever arranged their dashboard.

    ``None`` and ``[]`` are different answers: never touched, and deliberately cleared. If
    they were the same value, taking the last widget off the page would hand back the
    seven defaults, which is the opposite of what the person just asked for.
    """
    return getattr(profile, "dashboard_widgets", None) is not None


def keys_for(profile) -> list[str]:
    """The keys this person's dashboard shows, in their order.

    A widget whose key no longer exists — a plugin uninstalled, a widget dropped in an
    upgrade — is passed over rather than breaking the page.
    """
    if not has_arranged(profile):
        keys = default_keys()
    else:
        keys = list(profile.dashboard_widgets)
    return [key for key in keys if key in REGISTRY]


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
