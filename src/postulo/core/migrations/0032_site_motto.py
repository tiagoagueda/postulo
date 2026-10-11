from django.db import migrations, models
from django.db.models import F


def keep_what_the_footer_said(apps, schema_editor):
    """The footer used to print the tagline, so an instance's footer reads the same after this.

    The tagline stays what the home page and the web app's description say; the footer now
    has a line of its own, the motto (#835), and starts out as the tagline it was showing.
    """
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(motto="").exclude(tagline="").update(motto=F("tagline"))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0031_backup_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="motto",
            field=models.CharField(
                blank=True,
                help_text="The line in the footer of every page, after the name. Empty shows “your job search, your data”.",
                max_length=200,
                verbose_name="motto",
            ),
        ),
        migrations.AlterField(
            model_name="sitesettings",
            name="tagline",
            field=models.CharField(
                blank=True,
                help_text="A line under the name on the home page, and the web app's description. Optional.",
                max_length=200,
                verbose_name="tagline",
            ),
        ),
        migrations.RunPython(keep_what_the_footer_said, migrations.RunPython.noop),
    ]
