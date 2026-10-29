"""A sync link says which collection its twin lives in, which of a record's twins it is,
and whether a person undid the pairing (#326).

Nothing is copied: every existing link gets a blank collection and a blank slot, which is
what each of them was, the one twin of its record. The new rule that one remote item is
paired with one record applies only where the collection is set, so it reaches no existing
row until the plugin fills it in, after settling any two links that hold one UID
(postulo/postulo-dav#7).
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('plugins', '0012_register_lookup_starts_off'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='synclink',
            name='synclink_one_per_record',
        ),
        migrations.AddField(
            model_name='synclink',
            name='collection',
            field=models.CharField(blank=True, max_length=500, verbose_name='collection'),
        ),
        migrations.AddField(
            model_name='synclink',
            name='slot',
            field=models.CharField(blank=True, max_length=50, verbose_name='slot'),
        ),
        migrations.AddField(
            model_name='synclink',
            name='unpaired_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='unpaired'),
        ),
        migrations.AddIndex(
            model_name='synclink',
            index=models.Index(fields=['connection', 'collection', 'uid'], name='plugins_syn_connect_bc0c82_idx'),
        ),
        migrations.AddConstraint(
            model_name='synclink',
            constraint=models.UniqueConstraint(fields=('connection', 'content_type', 'object_id', 'slot'), name='synclink_one_per_record_slot'),
        ),
        migrations.AddConstraint(
            model_name='synclink',
            constraint=models.UniqueConstraint(condition=models.Q(models.Q(('collection', ''), _negated=True), models.Q(('uid', ''), _negated=True), ('unpaired_at__isnull', True)), fields=('connection', 'collection', 'uid'), name='synclink_one_record_per_item'),
        ),
    ]
