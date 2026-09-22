"""A tag's colour becomes one of seven, and a tag gains an icon (#285).

The colour column has been a free-text `CharField` since the first migration, inviting "a
hint for the interface, such as amber or sky". Nothing ever read it: every tag on every page
was drawn as the same grey pill. So there are rows out there holding `sky`, `slate`,
`emerald`, `#f59e0b`, `orange-ish` and the empty string, none of which has ever been seen by
anybody, and this is where they become one of the seven tones the stylesheet paints.

**Read for what they can mean, then left alone.** A value that names a colour becomes the
nearest tone in the palette — Tailwind's names because the help text as good as suggested
them, plain English ones because somebody not reading Tailwind's documentation would write
those. Everything else becomes grey, which is what it looked like yesterday and will look
like tomorrow: there is no colour to preserve, only a word nobody acted on.

The map is written out here rather than imported from `core.models`, which has its own copy.
That is not an oversight. A migration describes one moment; if the palette gains a colour or
`nearest_tone` learns a new synonym, this file must keep saying what happened on this day to
the rows that existed on this day, and an import would silently rewrite history.

Reversible, and the reverse is honest about being lossy: going back puts the tone name in
the column, which is a colour name in a free-text field and exactly what the field was for.
The icon has nowhere to go and is dropped with the column, which is what removing a field
means.
"""

from django.db import migrations, models

#: Frozen on 2026-09-22. See the note above before changing it.
NEAREST_TONE = {
    "sky": "blue", "cyan": "blue", "azure": "blue", "blue": "blue",
    "slate": "grey", "gray": "grey", "grey": "grey", "zinc": "grey",
    "stone": "grey", "neutral": "grey", "silver": "grey",
    "emerald": "green", "green": "green", "lime": "green",
    "teal": "teal", "turquoise": "teal",
    "amber": "amber", "yellow": "amber", "orange": "amber", "gold": "amber",
    "rose": "rose", "red": "rose", "pink": "rose", "crimson": "rose", "fuchsia": "rose",
    "violet": "violet", "purple": "violet", "indigo": "violet", "magenta": "violet",
}  # fmt: skip

COLOURS = [
    ("grey", "Grey"),
    ("blue", "Blue"),
    ("teal", "Teal"),
    ("green", "Green"),
    ("amber", "Amber"),
    ("rose", "Rose"),
    ("violet", "Violet"),
]

ICONS = [
    ("", "No icon"),
    ("home", "Remote"),
    ("map-pin", "Place"),
    ("users", "Referral"),
    ("star", "Favourite"),
    ("flag", "Flagged"),
    ("clock", "Waiting"),
    ("heart", "Liked"),
    ("briefcase", "Work"),
    ("graduation-cap", "Study"),
    ("banknote", "Pay"),
    ("globe", "Abroad"),
    ("shield", "Stable"),
]


def onto_the_palette(apps, schema_editor):
    """Every tag's colour becomes one of the seven, in as few queries as there are tones."""
    Tag = apps.get_model("core", "Tag")
    by_tone: dict[str, list[int]] = {}
    for pk, colour in Tag.objects.values_list("pk", "colour"):
        tone = NEAREST_TONE.get((colour or "").strip().lower(), "grey")
        by_tone.setdefault(tone, []).append(pk)
    for tone, pks in by_tone.items():
        Tag.objects.filter(pk__in=pks).update(colour=tone)


def leave_it_as_it_is(apps, schema_editor):
    """Nothing to undo: a tone name is a valid value for the free-text column it came from."""


class Migration(migrations.Migration):
    dependencies = [("core", "0020_errands_and_export_archives")]

    operations = [
        migrations.RunPython(onto_the_palette, leave_it_as_it_is),
        migrations.AlterField(
            model_name="tag",
            name="colour",
            field=models.CharField(
                choices=COLOURS,
                default="grey",
                help_text="A second signal beside the word, never instead of it.",
                max_length=20,
                verbose_name="colour",
            ),
        ),
        migrations.AddField(
            model_name="tag",
            name="icon",
            field=models.CharField(
                blank=True,
                choices=ICONS,
                default="",
                help_text="Optional, and decoration: the word is what is read aloud.",
                max_length=20,
                verbose_name="icon",
            ),
        ),
    ]
