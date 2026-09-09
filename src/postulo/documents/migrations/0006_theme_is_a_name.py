"""A theme stops being one of two choices and becomes a name that is checked.

``choices`` are frozen into every migration that touches the field, which makes them a
migration boundary: a theme arriving from an installed plugin is not known when a migration
is written, so it could never have been one of them (#132). The column keeps its type and
every existing row keeps its value — ``plain`` and ``classic`` are still the two themes
Postulo ships, and nothing about them changes here except who decides the list.

The validator travels with the column rather than living only in the form, so a theme that
does not set this kind of document is refused wherever it arrives from. ``max_length`` grows
from 20 to 60 for the same reason the list is no longer fixed: the names are somebody else's
to choose now, within a limit the column can hold.

Reversible without a data step. Going back re-freezes the two choices, and a row naming a
plugin's theme would then be a value outside them — which Django does not enforce at the
database level, so the row survives and starts being offered again the moment this is
re-applied.
"""

from django.db import migrations, models

import postulo.documents.themes


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0005_polymorphic_links"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cv",
            name="theme",
            field=models.CharField(
                default="plain",
                max_length=60,
                validators=[postulo.documents.themes.SetsThisKind("cv")],
                verbose_name="theme",
            ),
        ),
        migrations.AlterField(
            model_name="coverletter",
            name="theme",
            field=models.CharField(
                default="plain",
                max_length=60,
                validators=[postulo.documents.themes.SetsThisKind("letter")],
                verbose_name="theme",
            ),
        ),
    ]
