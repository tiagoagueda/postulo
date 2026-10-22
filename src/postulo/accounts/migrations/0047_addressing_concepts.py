from django.db import migrations

#: How each language wrote the form of address, the pronouns and the gender when this was
#: written (#921): the words a profile may hold, each with the concept it names. Written out
#: here rather than imported, because a migration describes one moment and the table beside
#: the code may change again. Sources are in `accounts/addressing.py`.
WORDS = {
    "form_of_address": {
        "en": {
            "mr": "Mr",
            "mrs": "Mrs",
            "miss": "Miss",
            "ms": "Ms",
            "mx": "Mx",
            "doctor": "Dr",
            "doctor_f": "Dr",
            "professor": "Prof.",
            "professor_f": "Prof.",
        },
        "fr": {
            "mr": "M.",
            "mrs": "Mme",
            "doctor": "Dr",
            "doctor_f": "Dr",
            "professor": "Pr",
            "professor_f": "Pr",
            "maitre": "Me",
        },
        "pt-PT": {
            "mr": "Sr.",
            "mrs": "Sr.ª",
            "doctor": "Dr.",
            "doctor_f": "Dr.ª",
            "engineer": "Eng.",
            "engineer_f": "Eng.ª",
            "professor": "Prof.",
            "professor_f": "Prof.ª",
        },
        "pt-BR": {
            "mr": "Sr.",
            "mrs": "Sra.",
            "doctor": "Dr.",
            "doctor_f": "Dra.",
            "professor": "Prof.",
            "professor_f": "Prof.ª",
        },
    },
    "pronouns": {
        "en": {"she": "she/her", "he": "he/him", "they": "they/them"},
        "fr": {"she": "elle", "he": "il", "they": "iel"},
        "pt-PT": {"she": "ela/dela", "he": "ele/dele", "they": "elu/delu"},
        "pt-BR": {"she": "ela/dela", "he": "ele/dele", "they": "elu/delu"},
    },
    "gender": {
        "en": {"female": "Female", "male": "Male", "non_binary": "Non-binary"},
        "fr": {"female": "Féminin", "male": "Masculin", "non_binary": "Non binaire"},
        "pt-PT": {"female": "Feminino", "male": "Masculino", "non_binary": "Não binário"},
        "pt-BR": {"female": "Feminino", "male": "Masculino", "non_binary": "Não binário"},
    },
}

#: A bare *Dr* or *Prof.* does not say, and is the unmarked concept (#921).
UNMARKED = {"doctor_f": "doctor", "professor_f": "professor", "engineer_f": "engineer"}

#: The gender words of before #864, each the concept its replacement is.
LEGACY_GENDERS = {
    "Woman": "female",
    "Man": "male",
    "Femme": "female",
    "Homme": "male",
    "Mulher": "female",
    "Homem": "male",
}

FIELDS = ("form_of_address", "pronouns", "gender")


def fold(text):
    return (text or "").strip().casefold().removesuffix(".").strip()


def index(field):
    seen = {}
    pairs = [(key, word) for table in WORDS[field].values() for key, word in table.items()]
    for key, word in sorted(pairs, key=lambda pair: pair[0] in UNMARKED):
        seen.setdefault(fold(word), key)
    for table in WORDS[field].values():
        for key in table:
            seen.setdefault(fold(key), key)
    if field == "gender":
        for word, key in LEGACY_GENDERS.items():
            seen.setdefault(fold(word), key)
    return seen


def to_concepts(apps, schema_editor):
    """A value that names a concept in any language becomes it; any other is *Other*, as typed."""
    Profile = apps.get_model("accounts", "Profile")
    for field in FIELDS:
        known = index(field)
        for value in list(
            Profile.objects.exclude(**{field: ""}).values_list(field, flat=True).distinct()
        ):
            concept = known.get(fold(value))
            if concept and concept != value:
                Profile.objects.filter(**{field: value}).update(**{field: concept})


def language_of(record_language):
    parts = (record_language or "").strip().casefold().split("-")
    if parts[0] == "fr":
        return "fr"
    if parts[0] == "pt":
        return "pt-BR" if parts[1:2] == ["br"] else "pt-PT"
    return "en"


def to_words(apps, schema_editor):
    """A concept becomes the word of the language of the profile's career record, else
    English's, else the first language that has one; text that is no concept stays."""
    Profile = apps.get_model("accounts", "Profile")
    for field in FIELDS:
        tables = WORDS[field]
        keys = {key for table in tables.values() for key in table}
        # Most specific first: pt-BR before pt, which would also take it.
        for code, own in ((c, language_of(c)) for c in ("", "fr", "pt-BR", "pt")):
            queryset = Profile.objects.filter(**{field + "__in": keys})
            if code:
                queryset = queryset.filter(record_language__istartswith=code)
            else:
                queryset = queryset.exclude(record_language__istartswith="fr").exclude(
                    record_language__istartswith="pt"
                )
            for key in keys:
                word = None
                for lang in (own, "en", "fr", "pt-PT"):
                    table = tables[lang]
                    word = table.get(key) or table.get(UNMARKED.get(key))
                    if word:
                        break
                if word:
                    queryset.filter(**{field: key}).update(**{field: word})


class Migration(migrations.Migration):
    """Store form of address, pronouns and gender as language-neutral concepts (#921).

    Where a value matches the word some catalogued language writes for a concept -- or the
    word the gender list used before #864 -- it becomes that concept (``Mme`` is ``mrs``, a
    bare ``Dr`` the unmarked ``doctor``). A value that matches none is *Other*, free text,
    and stays exactly as typed. Reversing writes the concept's word in the language of the
    career record, English where it names none.
    """

    dependencies = [
        ("accounts", "0046_identifier_primary"),
    ]

    operations = [
        migrations.RunPython(to_concepts, to_words),
    ]
