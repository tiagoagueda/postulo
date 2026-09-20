"""Somewhere for slow work, and for the archive it produces, to live (#247).

Two additive tables and nothing else: no column moves, and no data to carry across. An
instance that never switches a worker on still gets an errand row per press, which is what
the scheduler's reaper is for; the archives are reaped on their own expiry.
"""

import django.db.models.deletion
import postulo.core.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0019_carry_web_links_across"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExportArchive",
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
                (
                    "file",
                    models.FileField(
                        upload_to=postulo.core.models.archive_path, verbose_name="file"
                    ),
                ),
                ("filename", models.CharField(max_length=200, verbose_name="filename")),
                ("size", models.PositiveBigIntegerField(default=0, verbose_name="size")),
                ("expires_at", models.DateTimeField(db_index=True, verbose_name="expires at")),
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
                "verbose_name": "export archive",
                "verbose_name_plural": "export archives",
                "ordering": ("-created_at", "-pk"),
            },
        ),
        migrations.CreateModel(
            name="Errand",
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
                ("kind", models.CharField(max_length=40, verbose_name="kind")),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("waiting", "Waiting"),
                            ("working", "Working"),
                            ("done", "Done"),
                            ("failed", "Failed"),
                        ],
                        default="waiting",
                        max_length=10,
                        verbose_name="state",
                    ),
                ),
                (
                    "payload",
                    models.JSONField(blank=True, default=dict, verbose_name="what was asked"),
                ),
                (
                    "outcome",
                    models.JSONField(blank=True, default=dict, verbose_name="what came of it"),
                ),
                ("error", models.TextField(blank=True, verbose_name="what went wrong")),
                ("subject_id", models.PositiveBigIntegerField(blank=True, null=True)),
                (
                    "started_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="started at"),
                ),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="finished at"),
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
                (
                    "subject_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="contenttypes.contenttype",
                    ),
                ),
            ],
            options={
                "verbose_name": "errand",
                "verbose_name_plural": "errands",
                "ordering": ("-created_at", "-pk"),
                "indexes": [
                    models.Index(fields=["owner", "state"], name="core_errand_owner_i_1aa039_idx")
                ],
            },
        ),
    ]
