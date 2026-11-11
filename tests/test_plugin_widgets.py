"""A plugin's dashboard widget (#1102).

The dashboard was a grid of widgets a plugin could not add to: `plugins/api.py` said what a
widget is handed was deliberately not on the surface yet. It is now, as `DashboardWidget`,
and these are the promises that come with it: a widget is handed the person and their
connection to the plugin, never a queryset; it is offered and drawn only where the instance's
plugin policy lets that person use the plugin; it is asked on every view, as Postulo's own
widgets are; what its template writes is held to an allowlist; and one that raises costs its
own cell and nothing else on the page.

The plugin is `tests.widget_plugin`, written as a package outside the core would be.
"""

from __future__ import annotations

import logging

import pytest
from django.urls import reverse

from postulo.applications.models import Application
from postulo.core import widgets
from postulo.jobs.models import Company, JobPosting
from postulo.plugins.models import Connection, PluginPolicy
from tests.widget_plugin import Glance, registered

pytestmark = pytest.mark.django_db

KEY = "glance:at-a-glance"
HOME = "core:home"


@pytest.fixture(autouse=True)
def glance():
    with registered() as plugin:
        yield plugin


def a_connection(user, label="Phone", *, enabled=True) -> Connection:
    connection = Connection(
        owner=user,
        kind="sync",
        plugin="glance",
        label=label,
        enabled=enabled,
        config={"url": "https://dav.example.org"},
    )
    connection.save()
    return connection


def an_application(user, title="Engineer") -> Application:
    company = Company.objects.create(owner=user, name=f"Acme {title}")
    posting = JobPosting.objects.create(owner=user, title=title, company=company)
    return Application.objects.create(owner=user, posting=posting)


def on_the_dashboard(user, *keys) -> None:
    profile = user.profile
    profile.dashboard_widgets = list(keys)
    profile.save(update_fields=["dashboard_widgets"])


def dashboard(client, user, **params) -> str:
    client.force_login(user)
    response = client.get(reverse(HOME), params)
    assert response.status_code == 200
    return response.content.decode()


def cell(body: str) -> str:
    """The plugin's cell on the page, from its opening to its end."""
    start = body.index(f'data-widget="{KEY}"')
    return body[start : body.index("</section>", start)]


# ------------------------------------------------------------------ the surface


def test_the_surface_holds_the_widget_contract():
    """What a plugin declares a widget with is on the surface, and nowhere else is needed."""
    from postulo.plugins import api, base

    assert api.DashboardWidget is base.DashboardWidget
    assert "DashboardWidget" in api.__all__


def test_the_test_plugin_imports_only_the_surface():
    """It is written as a plugin outside the core would be, so it proves the surface suffices."""
    from pathlib import Path

    from postulo.plugins.testing import assert_imports_only_the_surface

    assert_imports_only_the_surface(
        Path(__file__).parent / "widget_plugin",
        package="tests.widget_plugin",
        allowed={"postulo.plugins.registry": "the tests' own switch that installs it"},
    )


# ------------------------------------------------------------------ offered


def test_a_plugins_widget_joins_the_registry_under_its_name():
    widget = widgets.get(KEY)

    assert widget is not None
    assert widget.provider == "glance"
    assert widget.label == "At a glance"
    assert widget.width == "half"
    assert KEY in widgets.all_keys()


def test_it_waits_on_the_arrange_page_under_the_plugins_name(client, user):
    """New to somebody whose dashboard was arranged before the plugin came: offered, not put on."""
    profile = user.profile
    profile.dashboard_known = [key for key in widgets.all_keys() if key != KEY]
    profile.save(update_fields=["dashboard_known"])

    body = dashboard(client, user, arrange="1")

    assert f'data-widget="{KEY}"' not in body
    assert "Add At a glance" in body
    assert [w.key for w in widgets.new_for(user.profile)] == [KEY]
    assert Glance.asked == [], "nothing is computed for a widget that is not on the page"


def test_adding_it_puts_it_on_the_page(client, user):
    client.force_login(user)

    client.post(reverse(HOME), {"action": "add", "key": KEY})

    user.profile.refresh_from_db()
    assert user.profile.dashboard_widgets[-1] == KEY


# ------------------------------------------------------------------ handed


def test_it_is_handed_the_person_and_their_connection(client, user, other_user):
    """The person and a connection, never a queryset; it reads through the surface's scoped
    reads, so it counts this person's applications and nobody else's."""
    connection = a_connection(user, "Nextcloud")
    a_connection(other_user, "Somebody else's")
    an_application(user)
    an_application(other_user, "Not theirs")
    on_the_dashboard(user, "counters", KEY)

    body = cell(dashboard(client, user))

    assert Glance.asked == [(user, connection)]
    assert "Syncing with Nextcloud." in body
    assert "1 applications" in body
    assert "At a glance" in body, "Postulo draws the heading"


def test_somebody_with_no_connection_is_handed_none(client, user):
    on_the_dashboard(user, KEY)

    body = cell(dashboard(client, user))

    assert Glance.asked == [(user, None)]
    assert "Not connected yet." in body


def test_each_connection_gets_its_own_body_under_its_name(client, user):
    first = a_connection(user, "Home")
    second = a_connection(user, "Work")
    a_connection(user, "Switched off", enabled=False)
    on_the_dashboard(user, KEY)

    body = cell(dashboard(client, user))

    assert [connection for _who, connection in Glance.asked] == [first, second]
    assert "Syncing with Home." in body
    assert "Syncing with Work." in body
    assert "Switched off" not in body


# ------------------------------------------------------------------ the policy


def test_a_plugin_switched_off_for_somebody_has_no_widget_for_them(client, user, other_user):
    """For them it is a plugin that is not installed: not drawn, not offered, not asked."""
    PluginPolicy.objects.create(plugin="glance", person=user, state=PluginPolicy.State.UNAVAILABLE)
    on_the_dashboard(user, "counters", KEY)
    on_the_dashboard(other_user, KEY)

    body = dashboard(client, user)
    arranging = dashboard(client, user, arrange="1")

    assert f'data-widget="{KEY}"' not in body
    assert "At a glance" not in arranging
    assert Glance.asked == []
    assert widgets.get(KEY, user) is None
    assert f'data-widget="{KEY}"' in dashboard(client, other_user), "the others keep it"


def test_a_switched_off_plugins_widget_cannot_be_added(client, user):
    PluginPolicy.objects.create(plugin="glance", person=user, state=PluginPolicy.State.UNAVAILABLE)
    client.force_login(user)

    client.post(reverse(HOME), {"action": "add", "key": KEY})

    user.profile.refresh_from_db()
    assert KEY not in user.profile.dashboard_widgets


def test_an_uninstalled_plugins_widget_is_passed_over(client, user, glance):
    from postulo.plugins import registry

    on_the_dashboard(user, "counters", KEY)
    registry.unregister_builtin("sync", glance)

    body = dashboard(client, user)

    assert 'data-widget="counters"' in body
    assert f'data-widget="{KEY}"' not in body


# ------------------------------------------------------------------ failing


def test_a_widget_that_raises_is_left_out_and_logged(client, user, caplog):
    """Somebody else's code on somebody's dashboard costs its own cell and nothing else."""
    Glance.fail_with = RuntimeError("the server is down")
    on_the_dashboard(user, "counters", KEY, "shortcuts")

    with caplog.at_level(logging.ERROR, logger="postulo.core.widgets"):
        body = dashboard(client, user)

    assert 'data-widget="counters"' in body
    assert 'data-widget="shortcuts"' in body
    assert f'data-widget="{KEY}"' not in body
    assert any(KEY in record.getMessage() for record in caplog.records)


def test_a_widget_that_answers_with_something_else_is_left_out(client, user):
    Glance.answer = Application.objects.none()
    on_the_dashboard(user, KEY, "shortcuts")

    body = dashboard(client, user)

    assert f'data-widget="{KEY}"' not in body
    assert 'data-widget="shortcuts"' in body


def test_a_failing_widget_can_still_be_taken_off_while_arranging(client, user):
    Glance.fail_with = RuntimeError("broken")
    on_the_dashboard(user, KEY)

    body = dashboard(client, user, arrange="1")

    assert f'data-widget="{KEY}"' in body
    assert "Take At a glance off" in body
    assert "Not connected yet." not in body


def test_a_declaration_that_cannot_be_used_is_logged_and_left_out(caplog):
    from postulo.plugins import registry
    from postulo.plugins.api import DashboardWidget

    class Careless:
        name = "careless"
        version = "0.1"
        kind = "sync"
        label = "Careless"
        widgets = (
            DashboardWidget(
                key="Not A Key", label="", blurb="", template="", context=None, width="wide"
            ),
            "not a widget",
        )

        def config_fields(self):
            return []

        def test(self, config):
            return None

        def sync(self, connection, config):
            return None

    registry.register_builtin("sync", Careless)
    try:
        with caplog.at_level(logging.WARNING, logger="postulo.core.widgets"):
            known = widgets.all_keys()
    finally:
        registry.unregister_builtin("sync", Careless)

    assert not [key for key in known if key.startswith("careless:")]
    assert KEY in known, "and nobody else's widget is lost with them"
    assert "cannot be used" in caplog.text


# ------------------------------------------------------------------ the markup


def test_what_the_template_writes_is_held_to_the_allowlist(client, user):
    """Whatever ``|safe`` let through: no script, handler, style, form, image, frame, id or
    ``data-`` attribute reaches the page, and words, classes and a link do."""
    Glance.extra = (
        '<script>alert("script")</script>'
        '<a href="javascript:alert(1)" onclick="alert(2)">bad link</a>'
        '<a href="/applications/" class="underline" data-widget-list>good link</a>'
        '<img src="https://tracker.example/pixel.png" onerror="alert(3)">'
        "<style>body { display: none }</style>"
        '<form action="/accounts/logout/" method="post"><button>Go</button></form>'
        '<iframe src="https://example.org"></iframe>'
        '<span id="main" style="position: fixed">kept words</span>'
        "<h1>too high</h1>"
    )
    on_the_dashboard(user, KEY)

    body = cell(dashboard(client, user))

    for gone in (
        "<script",
        'alert("script")',
        "javascript:",
        "onclick",
        "onerror",
        "<img",
        "<style",
        "display: none",
        "<form",
        "<button",
        "<iframe",
        'id="main"',
        "position: fixed",
        "data-widget-list",
        "data-count",
        "<h1",
    ):
        assert gone not in body, gone
    assert '<a href="/applications/" class="underline" rel="noopener noreferrer">good link</a>' in (
        body
    )
    assert "kept words" in body
    assert "too high" in body


def test_what_the_context_hands_the_template_is_escaped(client, user):
    """The template is escaped as every template is: a value is text, not markup."""
    a_connection(user, "<b>Home</b>")
    on_the_dashboard(user, KEY)

    body = cell(dashboard(client, user))

    assert "Syncing with &lt;b&gt;Home&lt;/b&gt;." in body


# ------------------------------------------------------------------ caching


def test_it_is_asked_on_every_view_as_postulos_own_widgets_are(client, user):
    """Nothing is kept between two views, so what changed shows on the next one."""
    on_the_dashboard(user, KEY)

    first = cell(dashboard(client, user))
    an_application(user)
    second = cell(dashboard(client, user))

    assert len(Glance.asked) == 2
    assert "0 applications" in first
    assert "1 applications" in second


def test_the_declarations_are_read_once_until_the_plugins_change(glance):
    """Read from the plugins the registry holds, and again only when they are new objects."""
    from postulo.plugins import registry

    widgets.all_keys()
    before = widgets._merged

    widgets.all_keys()
    assert widgets._merged is before

    registry.unregister_builtin("sync", glance)
    assert KEY not in widgets.all_keys()
    registry.register_builtin("sync", glance)
    assert KEY in widgets.all_keys()


# ------------------------------------------------------------------ the published check


def test_the_published_check_passes_a_usable_plugin():
    from postulo.plugins.testing import assert_declares_usable_widgets

    assert_declares_usable_widgets(Glance())


def test_the_published_check_names_what_is_wrong():
    from postulo.plugins.api import DashboardWidget
    from postulo.plugins.testing import assert_declares_usable_widgets

    class Wrong:
        name = "wrong"
        widgets = (
            DashboardWidget(
                key="ok", label="Fine", blurb="Fine.", template="nowhere.html", context=dict
            ),
            DashboardWidget(key="ok", label="", blurb="Fine.", template="x.html", context=dict),
        )

    with pytest.raises(AssertionError, match="has no label"):
        assert_declares_usable_widgets(Wrong())


def test_the_published_drawing_is_what_the_page_draws(user):
    from postulo.plugins.testing import draw_widget

    Glance.extra = '<script>alert(1)</script><a href="/applications/">link</a>'
    connection = a_connection(user, "Home")

    html = draw_widget(Glance(), "at-a-glance", user, connection)

    assert "Syncing with Home." in html
    assert "<script" not in html
    assert 'href="/applications/"' in html
    Glance.fail_with = ValueError("the plugin's own mistake")
    with pytest.raises(ValueError, match="own mistake"):
        draw_widget(Glance(), "at-a-glance", user, connection)
