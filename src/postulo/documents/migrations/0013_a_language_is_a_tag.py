"""The language a CV, a letter, an upload and a sent document declare is a BCP 47 tag in its
canonical form: `pt-BR`, not `pt-br` (#337).

The data step runs **before** the column changes, while it is still a plain `CharField`: what
it writes is then exactly what it means to write. Nothing it writes is longer than the old
column, so the order costs nothing. The new column has room for thirty-five characters, which
RFC 5646 section 4.4.1 advises and `ca-ES-valencia` needs.

A bare `sr` becomes `sr-Cyrl`. It was chosen from Postulo's own list, where it was the one
Serbian there was and is the Cyrillic catalogue; the list says so now.

Only the column. `source_text`, the page a sent document was drawn from, still says
`lang="pt-br"` where it did: it is the record of what was sent, and is not rewritten.
"""

import re

import postulo.core.language_field
from django.db import migrations

#: What a language tag has to look like, as `postulo.core.languages` held it on the day of
#: this migration. Frozen here and not imported: a migration is history, and has to do
#: tomorrow what it did the day it was written, whatever the application has learnt since.
#: `tests/test_language_migrations.py` holds it to the running one for as long as they agree.
_SHAPE = re.compile(
    r"[a-z]{2,3}(?:-[a-z]{3}){0,3}"
    r"(?:-[a-z]{4})?"
    r"(?:-(?:[a-z]{2}|[0-9]{3}))?"
    r"(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*"
    r"(?:-[0-9a-wy-z](?:-[a-z0-9]{2,8})+)*"
    r"(?:-x(?:-[a-z0-9]{1,8})+)?",
    re.ASCII | re.IGNORECASE,
)

#: What the list called Serbian until it said which script its catalogue is written in.
RENAMED = {"sr": "sr-Cyrl"}


def canonical(code):
    """A stored code as a BCP 47 tag in its canonical form: ``pt-BR`` from ``pt-br``.

    What is not shaped like a tag is handed back exactly as it is. It is somebody's data,
    and this migration is about how a tag is written, not about tidying.
    """
    if not isinstance(code, str):
        return code
    text = code.strip().replace("_", "-")
    if len(text) > 35 or not _SHAPE.fullmatch(text):
        return code
    first, *rest = text.split("-")
    written = [first.lower()]
    after_singleton = False
    for subtag in rest:
        after_singleton = after_singleton or len(subtag) == 1
        if not after_singleton and len(subtag) == 4 and subtag.isalpha():
            written.append(subtag.title())
        elif not after_singleton and len(subtag) == 2 and subtag.isalpha():
            written.append(subtag.upper())
        else:
            written.append(subtag.lower())
    tag = "-".join(written)
    return RENAMED.get(tag, tag)


def recase_columns(model, names) -> None:
    """Each named column of each row, rewritten where it changes. By primary key: once the
    column is a `LanguageField` a lookup by the old spelling would be prepared into the new."""
    for row in model.objects.values("pk", *names).iterator():
        new = {name: canonical(row[name]) for name in names if canonical(row[name]) != row[name]}
        if new:
            model.objects.filter(pk=row["pk"]).update(**new)


def recase(apps, schema_editor) -> None:
    for name in ("CV", "CoverLetter", "UploadedDocument", "RenderedDocument"):
        recase_columns(apps.get_model("documents", name), ("language",))


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0012_cv_printed_details'),
    ]

    operations = [
        migrations.RunPython(recase, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='coverletter',
            name='language',
            field=postulo.core.language_field.LanguageField(blank=True, help_text='Which language this letter is written in. Leave blank to follow your profile.', max_length=35, verbose_name='language'),
        ),
        migrations.AlterField(
            model_name='cv',
            name='language',
            field=postulo.core.language_field.LanguageField(blank=True, help_text='Which language this variant is written in. Leave blank to follow your profile.', max_length=35, verbose_name='language'),
        ),
        migrations.AlterField(
            model_name='rendereddocument',
            name='language',
            field=postulo.core.language_field.LanguageField(blank=True, editable=False, max_length=35, verbose_name='language'),
        ),
        migrations.AlterField(
            model_name='uploadeddocument',
            name='language',
            field=postulo.core.language_field.LanguageField(blank=True, help_text='Which language this file is in. Postulo cannot read it, so nothing is assumed; a store files it by this.', max_length=35, verbose_name='language'),
        ),
    ]
