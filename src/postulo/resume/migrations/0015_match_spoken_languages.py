"""Give a language somebody typed its code, where the name is exactly one language (#689).

Conservative: a name that two languages share, or that is the umbrella of several
(*Norwegian*, *Chinese*, *Portuguese*), stays the text it was, and so does one nobody
knows. Nothing here rewrites a ``name``, so what a printed CV says does not change; the
code is what lets the next one say *Inglês* where the person wrote *English*, and only
where the CV's language has the translation.
"""

from django.db import migrations

from postulo.core import language_names


def place(apps, schema_editor) -> None:
    model = apps.get_model("resume", "LanguageSkill")
    for row in model.objects.filter(code="").values("pk", "name").iterator():
        code = language_names.match(row["name"])
        if code:
            model.objects.filter(pk=row["pk"]).update(code=code)


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0014_language_by_code"),
    ]

    operations = [
        migrations.RunPython(place, migrations.RunPython.noop),
    ]
