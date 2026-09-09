"""What a career entry says in another language.

A row per entry, per language, per field, so that a second language is a translation rather
than a second career (#131). The unique constraint is the part a JSON map on the entry could
not have had: one text per field per language, decided by the database rather than by
whichever save happened to be last.

Nothing is written here. Every existing entry keeps saying exactly what it said, in whatever
language it was typed in, and a CV that declares no language is unaffected -- a translation
is something somebody adds, never something inferred from text already stored.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("resume", "0003_links"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Translation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="created at"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("object_id", models.PositiveIntegerField()),
                ("language", models.CharField(max_length=10, verbose_name="language")),
                ("field", models.CharField(max_length=40, verbose_name="field")),
                ("text", models.TextField(blank=True, verbose_name="text")),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="contenttypes.contenttype",
                        verbose_name="kind of entry",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_set",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="owner",
                    ),
                ),
            ],
            options={
                "verbose_name": "translation",
                "verbose_name_plural": "translations",
                "ordering": ("language", "field"),
                "indexes": [
                    models.Index(
                        fields=["content_type", "object_id", "language"],
                        name="resume_tran_content_7a2bda_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("content_type", "object_id", "language", "field"),
                        name="resume_one_text_per_field_per_language",
                    )
                ],
            },
        ),
    ]
