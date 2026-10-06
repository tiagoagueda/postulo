"""Resume 0011 moves the text the Europass import wrote into the level it names (#684).

Exactly "EQF 1" to "EQF 8" in the grade becomes the level and the grade is cleared; anything
else a person wrote there -- "6.0 GPA", "EQF 9", "eqf 6" -- stays where it is, with its
translations.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("resume", "0011_an_education_entry_states_its_eqf_level")]
AFTER = [("resume", "0012_move_an_eqf_grade_to_the_level")]

pytestmark = pytest.mark.django_db(transaction=True)


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    # The state of everything now applied, not of the targets' ancestors alone: the user
    # table is the database's, whichever migration of its own app it has reached.
    loader = MigrationExecutor(connection).loader
    return loader.project_state(list(loader.applied_migrations)).apps


def test_an_exact_eqf_grade_moves_and_any_other_stays():
    old = migrate(BEFORE)
    user = old.get_model("accounts", "User").objects.create(
        email="mover@example.org", username="mover"
    )
    Education = old.get_model("resume", "Education")
    Translation = old.get_model("resume", "Translation")
    ContentType = old.get_model("contenttypes", "ContentType")
    kind, _made = ContentType.objects.get_or_create(app_label="resume", model="education")

    def entry(grade, **more):
        return Education.objects.create(
            owner=user, institution="U", qualification=grade or "none", grade=grade, **more
        )

    moved = entry("EQF 6")
    last = entry("EQF 8")
    gpa = entry("6.0 GPA")
    nine = entry("EQF 9")
    lower = entry("eqf 6")
    spaced = entry("EQF 6 ")
    stated = entry("EQF 5", eqf_level=7)
    for held in (moved, gpa):
        Translation.objects.create(
            owner=user,
            content_type=kind,
            object_id=held.pk,
            language="fr-FR",
            field="grade",
            text="x",
        )

    new = migrate(AFTER)
    rows = new.get_model("resume", "Education").objects

    def state(pk):
        row = rows.get(pk=pk)
        return row.grade, row.eqf_level

    assert state(moved.pk) == ("", 6)
    assert state(last.pk) == ("", 8)
    assert state(gpa.pk) == ("6.0 GPA", None)
    assert state(nine.pk) == ("EQF 9", None)
    assert state(lower.pk) == ("eqf 6", None)
    assert state(spaced.pk) == ("EQF 6 ", None)
    assert state(stated.pk) == ("EQF 5", 7), "a level already stated is not overwritten"
    left = new.get_model("resume", "Translation").objects.filter(field="grade")
    assert [row.object_id for row in left] == [gpa.pk], "only a grade that was not moved keeps its"

    # Leave the database at the latest migration for whatever runs next.
    MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
