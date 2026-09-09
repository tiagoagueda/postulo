"""A scheme is a key checked against a registry, not one of a frozen list of choices.

``choices`` are written into every migration that touches the field, which made them a
boundary: a scheme contributed by a plugin could never have been one of them (#109). The
keys do not change -- they were already distinct across the two registries this merges,
except ``other``, which stays ``other`` -- so every existing row keeps its value and nothing
is rewritten here.

What replaces the choices is stricter rather than looser. The validator asks the registry
whether the scheme exists *and* whether it identifies this kind of thing, so a company's
identifier on a person is refused rather than merely absent from a menu.
"""

import postulo.core.identifiers
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0013_record_language"),
    ]

    operations = [
        migrations.AlterField(
            model_name="personidentifier",
            name="scheme",
            field=models.CharField(
                max_length=20,
                validators=[postulo.core.identifiers.Identifies("person")],
                verbose_name="scheme",
            ),
        ),
    ]
