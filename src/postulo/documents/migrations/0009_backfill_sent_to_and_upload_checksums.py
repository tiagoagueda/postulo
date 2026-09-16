"""What the new columns hold for rows that existed before them (#217).

`sent_to` is what keeps a frozen PDF meaningful once the application it went with is deleted,
so a render written before the column existed needs it filled from the link it still has.
The English "at" is deliberate here and only here: a migration has no reader and no language,
while `rendering.sent_to` writes it in the language the document was made in.

Checksums are read from the files themselves. An upload is capped at a few megabytes and
this runs once; a file that has gone missing is skipped rather than failing the migration,
because a backup restored without its media is a situation to survive, not to refuse.
"""

from __future__ import annotations

import hashlib

from django.db import migrations


def fill(apps, schema_editor) -> None:
    rendered = apps.get_model("documents", "RenderedDocument")
    uploaded = apps.get_model("documents", "UploadedDocument")

    for render in (
        rendered.objects.filter(sent_to="", application__isnull=False)
        .select_related("application__posting__company")
        .iterator()
    ):
        posting = render.application.posting
        render.sent_to = f"{posting.title} at {posting.company.name}"[:250]
        render.save(update_fields=["sent_to"])

    for upload in uploaded.objects.filter(checksum="").iterator():
        if not upload.file:
            continue
        digest = hashlib.sha256()
        try:
            with upload.file.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except (OSError, ValueError):
            continue
        upload.checksum = digest.hexdigest()
        upload.save(update_fields=["checksum"])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0008_rendereddocument_sent_to_uploadeddocument_checksum_and_more"),
    ]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
