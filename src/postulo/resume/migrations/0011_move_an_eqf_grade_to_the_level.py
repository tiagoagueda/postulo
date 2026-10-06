"""An exact "EQF 1" to "EQF 8" in an education entry's grade is the level it names (#684).

It is the one text the Europass import ever wrote there, and only where the file gave no
grade. It moves into ``eqf_level`` and the grade is cleared; a translation of a grade that
moved is a translation of nothing now, and goes with it. Any other grade -- "6.0 GPA", "EQF 9",
"eqf 6" -- is somebody's own wording and is left alone, with its translations. An entry that
already states a level keeps it, and keeps its grade too.

Going back writes the text again where an entry has a level and no grade.
"""

import re

from django.db import migrations

#: Exactly what the importer wrote, and nothing it might have been typed as.
_WRITTEN = re.compile(r"EQF ([1-8])")


def move_to_the_level(apps, schema_editor):
    Education = apps.get_model("resume", "Education")
    Translation = apps.get_model("resume", "Translation")
    ContentType = apps.get_model("contenttypes", "ContentType")
    kind = ContentType.objects.filter(app_label="resume", model="education").first()
    moved = []
    for entry in Education.objects.filter(eqf_level__isnull=True, grade__startswith="EQF "):
        match = _WRITTEN.fullmatch(entry.grade)
        if match is None:
            continue
        entry.eqf_level = int(match.group(1))
        entry.grade = ""
        entry.save(update_fields=["eqf_level", "grade"])
        moved.append(entry.pk)
    if moved and kind is not None:
        Translation.objects.filter(content_type=kind, object_id__in=moved, field="grade").delete()


def write_it_as_the_grade(apps, schema_editor):
    Education = apps.get_model("resume", "Education")
    for entry in Education.objects.filter(eqf_level__isnull=False, grade=""):
        entry.grade = f"EQF {entry.eqf_level}"
        entry.save(update_fields=["grade"])


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("resume", "0010_an_education_entry_states_its_eqf_level"),
    ]

    operations = [migrations.RunPython(move_to_the_level, write_it_as_the_grade)]
