from django.db import migrations

#: The words the gender list offered, each with the word that replaced it (#864). Written out
#: here rather than imported, because a migration describes one moment and the table beside
#: the code may change again.
RENAMED = {
    "Woman": "Female",
    "Man": "Male",
    "Femme": "Féminin",
    "Homme": "Masculin",
    "Mulher": "Feminino",
    "Homem": "Masculino",
}


def rename(apps, mapping):
    Profile = apps.get_model("accounts", "Profile")
    for old, new in mapping.items():
        Profile.objects.filter(gender=old).update(gender=new)


def to_new_words(apps, schema_editor):
    rename(apps, RENAMED)


def to_old_words(apps, schema_editor):
    rename(apps, {new: old for old, new in RENAMED.items()})


class Migration(migrations.Migration):
    """Put the new gender words in a profile that holds an old one (#864).

    Only an exact match changes: the text somebody typed under *Other…* is theirs, and so is a
    word this table does not know. Each old word belongs to one language, so no language is
    needed to map it.
    """

    dependencies = [
        ("accounts", "0038_address_claims"),
    ]

    operations = [
        migrations.RunPython(to_new_words, to_old_words),
    ]
