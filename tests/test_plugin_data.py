"""What happens to a plugin's table when the plugin goes (#128).

No plugin owns a table today, which is exactly why the rule is written now: everything the
newest plugin governs lives in core, so the question has never had to be answered, and it has
to be answered *before* a plugin is moved out carrying a table with it.

**The rule.** A plugin that owns a table may not be uninstalled while that table holds
anything. Postulo refuses, names how many records are in the way, and leaves both the package
and the data alone.

The two rejected options are worth restating, because a future reader will reach for one of
them. *Keep the table* leaves data nothing can read, export or restore — the failure the
export exists to prevent. *Delete it behind a confirmation* makes removing a package a
data-destroying act, when somebody may only be swapping it for a newer build, and it puts
*uninstall* on the wrong side of the promise that switching a plugin off deletes nothing.

The plugin here claims to own `core.Tag`. Nothing about the mechanism cares where a model
lives — it deals in labels — and borrowing a real one exercises every part of this without
inventing an app that would exist only for a test.
"""

from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from postulo.core.models import Tag
from postulo.plugins import data, installing, registry
from postulo.plugins.base import FieldSpec
from postulo.plugins.base import TestResult as PluginTestResult

pytestmark = pytest.mark.django_db

DISTRIBUTION = "postulo-tagger"


class Tagger:
    """A plugin that owns a table, which no plugin Postulo ships actually does."""

    name = "tagger"
    version = "1.0.0"
    kind = "notifier"
    label = "Tagger"
    description = "Owns some rows."
    owns_models = ("core.Tag",)

    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def send(self, message, config: dict) -> bool:
        return True


class Stateless:
    """The other kind: a plugin that holds nothing, and can go whenever it likes."""

    name = "stateless"
    version = "1.0.0"
    kind = "notifier"
    label = "Stateless"
    description = "Owns nothing."

    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def send(self, message, config: dict) -> bool:
        return True


@contextlib.contextmanager
def installed_from(plugin_class, distribution: str = DISTRIBUTION):
    """The plugin registered, and the package it claims to have come from."""
    registry.register_builtin("notifier", plugin_class)
    registry.plugins("notifier", refresh=True)
    module = plugin_class.__module__.split(".")[0]
    with mock.patch(
        "importlib.metadata.packages_distributions", return_value={module: [distribution]}
    ):
        try:
            yield
        finally:
            registry.unregister_builtin("notifier", plugin_class)
            registry.plugins("notifier", refresh=True)


# ------------------------------------------------------------ what a plugin declares


def test_a_plugin_says_what_it_owns():
    assert data.owned_labels(Tagger()) == ("core.Tag",)


def test_most_plugins_own_nothing():
    """Every plugin Postulo ships, and every stateless kind."""
    assert data.owned_labels(Stateless()) == ()


def test_one_label_may_be_written_without_a_tuple():
    class Careless:
        owns_models = "core.Tag"

    assert data.owned_labels(Careless()) == ("core.Tag",)


def test_a_label_whose_app_is_missing_is_passed_over_rather_than_raising():
    """A start-up problem with its own message, not a reason the plugins page cannot render."""

    class Absent:
        owns_models = ("nowhere.Nothing",)

    assert data.owned_models(Absent()) == []
    assert data.rows_held_by(Absent()) == {}


# ----------------------------------------------------------------- counting the rows


def test_an_empty_table_holds_nothing(db):
    assert data.rows_held_by(Tagger()) == {}


def test_rows_are_counted_by_model(user):
    Tag.objects.create(owner=user, name="one")
    Tag.objects.create(owner=user, name="two")

    assert data.rows_held_by(Tagger()) == {"core.Tag": 2}


def test_everybody_s_rows_count_not_just_one_person_s(user, django_user_model):
    """Uninstalling is an instance act, so the question is instance-wide."""
    other = django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )
    Tag.objects.create(owner=user, name="mine")
    Tag.objects.create(owner=other, name="theirs")

    assert data.rows_held_by(Tagger()) == {"core.Tag": 2}


# --------------------------------------------------------------------- the refusal


def test_a_package_holding_nothing_may_go(db):
    with installed_from(Tagger):
        assert data.refuse_removing(DISTRIBUTION) == ""


def test_a_package_holding_something_may_not(user):
    Tag.objects.create(owner=user, name="one")

    with installed_from(Tagger):
        refusal = data.refuse_removing(DISTRIBUTION)

    assert refusal
    assert "1 record" in refusal, "the count, because 'there is data' is not actionable"
    assert "Tagger" in refusal, "named after what it protects"


def test_the_refusal_says_what_to_do_instead(user):
    Tag.objects.create(owner=user, name="one")

    with installed_from(Tagger):
        refusal = data.refuse_removing(DISTRIBUTION)

    assert "switch the plugin off instead" in refusal
    assert "off keeps everything" in refusal


def test_a_package_that_owns_nothing_is_never_held(user):
    Tag.objects.create(owner=user, name="one")

    with installed_from(Stateless):
        assert data.refuse_removing(DISTRIBUTION) == ""


def test_another_package_is_not_held_by_this_one_s_rows(user):
    Tag.objects.create(owner=user, name="one")

    with installed_from(Tagger):
        assert data.refuse_removing("postulo-something-else") == ""


# ----------------------------------------------- the refusal is the last door, not the first


def test_remove_itself_refuses(user, tmp_path, settings):
    """A management command or a shell reaching `remove()` must meet the same rule."""
    Tag.objects.create(owner=user, name="one")
    settings.POSTULO_PLUGINS_DIR = str(tmp_path)
    installing.write_record(
        [installing.Installed(name=DISTRIBUTION, version="1.0.0", installed_at="", entry_points=[])]
    )

    with installed_from(Tagger), pytest.raises(installing.InstallError) as raised:
        installing.remove(DISTRIBUTION)

    assert "1 record" in str(raised.value)


def test_emptying_it_lets_the_package_go(user, tmp_path, settings):
    Tag.objects.create(owner=user, name="one")
    settings.POSTULO_PLUGINS_DIR = str(tmp_path)
    installing.write_record(
        [installing.Installed(name=DISTRIBUTION, version="1.0.0", installed_at="", entry_points=[])]
    )
    Tag.objects.all().delete()

    with installed_from(Tagger):
        entry = installing.remove(DISTRIBUTION)

    assert entry.name == DISTRIBUTION


# ------------------------------------------------------------------- the archive


def test_a_plugin_that_owns_data_and_cannot_export_it_is_named(user):
    Tag.objects.create(owner=user, name="one")

    with installed_from(Tagger):
        section = data.export_sections(user)

    assert section["not_carried"] == ["core.Tag"]


def test_a_plugin_that_can_export_puts_its_rows_in(user):
    class Exporting(Tagger):
        name = "exporting"

        def export_for(self, person) -> list[dict]:
            return [{"name": row.name} for row in Tag.objects.filter(owner=person)]

    Tag.objects.create(owner=user, name="one")

    with installed_from(Exporting):
        section = data.export_sections(user)

    assert section["carried"]["exporting"] == [{"name": "one"}]
    assert "not_carried" not in section


def test_a_plugin_that_owns_nothing_is_not_in_the_archive_at_all(user):
    with installed_from(Stateless):
        section = data.export_sections(user)

    assert section == {"carried": {}}


def test_a_plugin_that_raises_while_exporting_is_named_rather_than_fatal(user):
    """Somebody's archive is worth more than one plugin's tidiness."""

    class Broken(Tagger):
        name = "broken"

        def export_for(self, person):
            raise RuntimeError("no")

    with installed_from(Broken):
        section = data.export_sections(user)

    assert section["not_carried"] == ["core.Tag"]


def test_the_document_carries_the_section(user):
    from postulo.core.export import build_document

    document = build_document(user)

    assert "plugins" in document
    assert document["postulo"]["format"] == 8
