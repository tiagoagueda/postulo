"""Accounts 0039 puts the new gender words where the old ones were (#864).

An exact old word becomes the one that replaced it; anything else -- the text somebody typed
under *Other…*, a word the table does not know, an old word with another case or a space --
stays as it was typed. Reversing puts the old words back.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("accounts", "0038_address_claims")]
AFTER = [("accounts", "0039_gender_words")]

pytestmark = pytest.mark.django_db(transaction=True)

OLD_TO_NEW = {
    "Woman": "Female",
    "Man": "Male",
    "Femme": "Féminin",
    "Homme": "Masculin",
    "Mulher": "Feminino",
    "Homem": "Masculino",
}
LEFT_ALONE = ["Non-binary", "Non binaire", "Não binário", "agender", "woman", "Man ", ""]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    loader = MigrationExecutor(connection).loader
    return loader.project_state(list(loader.applied_migrations)).apps


def test_an_old_word_becomes_the_new_one_and_anything_else_stays_and_it_reverses():
    old = migrate(BEFORE)
    User = old.get_model("accounts", "User")
    Profile = old.get_model("accounts", "Profile")
    held = {}
    for index, word in enumerate([*OLD_TO_NEW, *LEFT_ALONE]):
        user = User.objects.create(email=f"p{index}@example.org", username=f"p{index}")
        profile, _made = Profile.objects.get_or_create(user=user)
        Profile.objects.filter(pk=profile.pk).update(gender=word)
        held[word] = profile.pk

    new = migrate(AFTER)
    Profile = new.get_model("accounts", "Profile")
    for word, pk in held.items():
        assert Profile.objects.get(pk=pk).gender == OLD_TO_NEW.get(word, word), word

    back = migrate(BEFORE)
    Profile = back.get_model("accounts", "Profile")
    for word, pk in held.items():
        assert Profile.objects.get(pk=pk).gender == word, word

    # Leave the database at the latest migration for whatever runs next.
    MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
