"""A sync plugin with a dashboard widget, written as a plugin outside the core would be (#1102).

It imports the plugin surface and nothing else, keeps its template in a ``templates/``
directory beside it, and is registered by the tests that want it with `registered()`. Shared
by `tests/test_plugin_widgets.py` and the browser tests of the dashboard.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import ClassVar

from postulo.plugins.api import (
    Application,
    DashboardWidget,
    FieldSpec,
    SyncReport,
    TestResult,
)

#: What the widget's template prints unescaped. A tidy link by default; a test puts worse in.
LINK = '<a href="/applications/" class="underline">See them all</a>'


def _at_a_glance(user, connection) -> dict:
    Glance.asked.append((user, connection))
    if Glance.fail_with is not None:
        raise Glance.fail_with
    if Glance.answer is not None:
        return Glance.answer
    return {
        "where": connection.label if connection is not None else "",
        "applications": Application.objects.for_user(user).count(),
        "extra": Glance.extra,
    }


class Glance:
    """A sync whose widget says where it syncs and how many applications there are."""

    name = "glance"
    version = "0.1"
    kind = "sync"
    label = "Glance"

    #: Every call to the widget's context, as ``(user, connection)``.
    asked: ClassVar[list] = []
    #: An exception the context raises, if any.
    fail_with: ClassVar[BaseException | None] = None
    #: What the context returns instead of its own answer, if anything.
    answer: ClassVar[object] = None
    #: What the template prints with ``|safe``.
    extra: ClassVar[str] = LINK

    widgets = (
        DashboardWidget(
            key="at-a-glance",
            label="At a glance",
            blurb="Where Glance syncs, and how many applications it sees.",
            template="widget_plugin/at_a_glance.html",
            context=_at_a_glance,
            width="half",
        ),
    )

    def config_fields(self):
        return [FieldSpec("url", "Server", type="url")]

    def test(self, config):
        return TestResult(True, "")

    def sync(self, connection, config):
        return SyncReport()


def reset() -> None:
    Glance.asked = []
    Glance.fail_with = None
    Glance.answer = None
    Glance.extra = LINK


@contextmanager
def registered():
    """Glance installed for the length of the block, as a built-in sync."""
    from postulo.plugins import registry

    reset()
    registry.register_builtin("sync", Glance)
    try:
        yield Glance
    finally:
        registry.unregister_builtin("sync", Glance)
        reset()
