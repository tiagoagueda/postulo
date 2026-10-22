"""Accounts 0047 stores form of address, pronouns and gender as concepts (#921).

A value that is the word some catalogued language writes for a concept -- or the gender word
from before #864 -- becomes that concept; a value that is none stays exactly as typed, which is
*Other*. Reversing writes each concept as its word in the language of the career record.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("accounts", "0046_identifier_primary")]
AFTER = [("accounts", "0047_addressing_concepts")]

pytestmark = pytest.mark.django_db(transaction=True)

#: (form_of_address, pronouns, gender, record_language) before, and the same after.
ROWS = {
    "english": (("Mx", "they/them", "Non-binary", "en-GB"), ("mx", "they", "non_binary")),
    "french": (("Mme", "iel", "Féminin", "fr-FR"), ("mrs", "they", "female")),
    "portuguese": (("Eng.ª", "ela/dela", "Mulher", "pt-PT"), ("engineer_f", "she", "female")),
    "brazilian": (("Dra.", "ele/dele", "Homem", "pt-BR"), ("doctor_f", "he", "male")),
    "bare Dr": (("Dr", "", "", ""), ("doctor", "", "")),
    "bare Prof.": (("Prof.", "", "", ""), ("professor", "", "")),
    "Other": (("Rev", "xe/xem", "agender", "en-GB"), ("Rev", "xe/xem", "agender")),
    "blank": (("", "", "", ""), ("", "", "")),
}

#: What going back writes: the word in the language of the career record (English where it
#: names none, or has no word), for the concepts that are left.
BACK = {
    "english": ("Mx", "they/them", "Non-binary"),
    "french": ("Mme", "iel", "Féminin"),
    "portuguese": ("Eng.ª", "ela/dela", "Feminino"),
    "brazilian": ("Dra.", "ele/dele", "Masculino"),
    "bare Dr": ("Dr", "", ""),
    "bare Prof.": ("Prof.", "", ""),
    "Other": ("Rev", "xe/xem", "agender"),
    "blank": ("", "", ""),
}


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    loader = MigrationExecutor(connection).loader
    return loader.project_state(list(loader.applied_migrations)).apps


def held(apps, pk):
    profile = apps.get_model("accounts", "Profile").objects.get(pk=pk)
    return profile.form_of_address, profile.pronouns, profile.gender


def test_words_become_concepts_other_stays_and_it_reverses():
    old = migrate(BEFORE)
    User = old.get_model("accounts", "User")
    Profile = old.get_model("accounts", "Profile")
    made = {}
    for index, (name, ((form, pronouns, gender, language), _after)) in enumerate(ROWS.items()):
        user = User.objects.create(email=f"p{index}@example.org", username=f"p{index}")
        profile, _made = Profile.objects.get_or_create(user=user)
        Profile.objects.filter(pk=profile.pk).update(
            form_of_address=form, pronouns=pronouns, gender=gender, record_language=language
        )
        made[name] = profile.pk

    new = migrate(AFTER)
    for name, pk in made.items():
        assert held(new, pk) == ROWS[name][1], name

    back = migrate(BEFORE)
    for name, pk in made.items():
        assert held(back, pk) == BACK[name], name

    # Forward again from the reversed data is the same as the first time.
    again = migrate(AFTER)
    for name, pk in made.items():
        assert held(again, pk) == ROWS[name][1], name

    # Leave the database at the latest migration for whatever runs next.
    MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
