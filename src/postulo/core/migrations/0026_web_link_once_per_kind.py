"""An address may be listed once under each kind of link, rather than once per holder (#457).

The constraint said once per holder whatever the kind, and every check in front of it looked
at one kind at a time, so a GitHub profile listed under *Code repositories* and under
*Websites* passed them all and then broke the constraint, with a 500 and nothing saved. One
address can honestly be two things, so the rule is narrowed to what the checks already said.

Narrowing admits more than before and refuses nothing that was admitted, so no row has to
change. Going back puts the wider rule on again, which fails if anybody has since listed one
address under two kinds; that is what widening a uniqueness rule over data costs.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0025_weblink_service"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="weblink",
            name="web_link_unique_per_holder",
        ),
        migrations.AddConstraint(
            model_name="weblink",
            constraint=models.UniqueConstraint(
                fields=("content_type", "object_id", "kind", "url"),
                name="web_link_unique_per_holder_and_kind",
            ),
        ),
    ]
