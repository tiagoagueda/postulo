"""Resume 0011 gives a typed language its code where the name is exactly one language (#689).

Conservative, and it never rewrites a name: *French*, *Francês* and *français* are French;
*Norwegian* is Bokmål or Nynorsk and stays the text it was, as does a word nobody knows.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("resume", "0010_language_by_code")]
AFTER = [("resume", "0011_match_spoken_languages")]


@pytest.mark.django_db(transaction=True)
def test_a_typed_name_that_is_one_language_is_placed_and_the_rest_are_left_alone():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    # The real model: only `resume` is taken back, so the account tables are the current ones.
    user = get_user_model().objects.create_user(email="speaker@example.org", password="x")
    model = old.get_model("resume", "LanguageSkill")
    for name in ("French", "Francês", "français", "Norwegian", "Elvish", "Portuguese"):
        model.objects.create(owner_id=user.pk, name=name, proficiency="b2")
    model.objects.create(owner_id=user.pk, name="English", code="de", proficiency="b2")

    executor = MigrationExecutor(connection)
    executor.migrate(AFTER)
    new = executor.loader.project_state(AFTER).apps

    rows = {row.name: row.code for row in new.get_model("resume", "LanguageSkill").objects.all()}
    assert rows == {
        "French": "fr",
        "Francês": "fr",
        "français": "fr",
        "Norwegian": "",
        "Elvish": "",
        "Portuguese": "",
        "English": "de",
    }, "a name is never rewritten, and a code somebody chose is never replaced"

    # Leave the database at the latest migration for whatever runs next.
    MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@pytest.mark.django_db
def test_the_command_reports_what_matched_and_what_it_left(user, capsys):
    from postulo.resume.models import LanguageSkill

    LanguageSkill.objects.create(owner=user, name="Francês", proficiency="b2")
    LanguageSkill.objects.create(owner=user, name="Norwegian", proficiency="b2")
    LanguageSkill.objects.create(owner=user, name="English", code="en", proficiency="b2")

    call_command("match_languages")
    said = capsys.readouterr().out

    assert "1 entries already have a code" in said
    assert "Would set fr on 1 entry named 'Francês'" in said
    assert "Left as text: 'Norwegian'" in said
    assert LanguageSkill.objects.get(name="Francês").code == ""

    call_command("match_languages", "--apply")

    assert LanguageSkill.objects.get(name="Francês").code == "fr"
    assert LanguageSkill.objects.get(name="Norwegian").code == ""
