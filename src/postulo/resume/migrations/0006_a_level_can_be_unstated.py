"""A language can say that its level was never stated (#235).

A CEFR level is a claim about yourself that somebody will test in an interview, and there
was no way of declining to make one: the column had no blank among its choices, so the
Europass import filled every language the file gave no level for with B1 -- a claim Postulo
invented on the person's behalf and then printed on their CV.

Nothing already stored changes. The default is still B2, because somebody adding a language
by hand is saying something about themselves; what is new is that leaving it unsaid is one
of the things they are allowed to say, and it prints nothing at all.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0005_the_order_is_the_persons"),
    ]

    operations = [
        migrations.AlterField(
            model_name="languageskill",
            name="proficiency",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not stated"),
                    ("a1", "A1 — beginner"),
                    ("a2", "A2 — elementary"),
                    ("b1", "B1 — intermediate"),
                    ("b2", "B2 — upper intermediate"),
                    ("c1", "C1 — advanced"),
                    ("c2", "C2 — proficient"),
                    ("native", "Native"),
                ],
                default="b2",
                max_length=10,
                verbose_name="proficiency",
            ),
        ),
    ]
