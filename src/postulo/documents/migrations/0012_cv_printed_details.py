"""A CV says which of its owner's details it prints (#308).

One answer per kind of detail -- a telephone number, an email address, a link of each of
the three kinds, the identifiers -- and three switches: the location, the form of address
and the pronouns.

**Nothing is filled in, because every default is what a CV already printed.** Each answer
starts as *default*, which follows the profile: the primary number, the account's address,
the primary link of each kind, every identifier. The location starts on, and the form of
address and the pronouns start off, which is what #309 promised when it put them on the
profile. So a CV made before this is drawn exactly as it was, and no row needs touching.

The pinned email address is allauth's row, which is why this depends on its `account` app.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0009_emailaddress_unique_primary_email'),
        ('accounts', '0027_profile_form_of_address_and_pronouns'),
        ('core', '0023_capture_keeps_the_page'),
        ('documents', '0011_snapshot_plain_text_and_local_exports'),
    ]

    operations = [
        migrations.AddField(
            model_name='cv',
            name='email_choice',
            field=models.CharField(choices=[('default', 'Follow your details'), ('chosen', 'Chosen for this CV'), ('none', 'None')], default='default', max_length=10, verbose_name='email address printed'),
        ),
        migrations.AddField(
            model_name='cv',
            name='identifiers_choice',
            field=models.CharField(choices=[('default', 'Follow your details'), ('chosen', 'Chosen for this CV'), ('none', 'None')], default='default', max_length=10, verbose_name='identifiers printed'),
        ),
        migrations.AddField(
            model_name='cv',
            name='phone_choice',
            field=models.CharField(choices=[('default', 'Follow your details'), ('chosen', 'Chosen for this CV'), ('none', 'None')], default='default', max_length=10, verbose_name='telephone number printed'),
        ),
        migrations.AddField(
            model_name='cv',
            name='pinned_email',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='account.emailaddress', verbose_name='email address chosen'),
        ),
        migrations.AddField(
            model_name='cv',
            name='pinned_identifiers',
            field=models.ManyToManyField(blank=True, related_name='+', to='accounts.personidentifier', verbose_name='identifiers chosen'),
        ),
        migrations.AddField(
            model_name='cv',
            name='pinned_phone',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.phonenumber', verbose_name='telephone number chosen'),
        ),
        migrations.AddField(
            model_name='cv',
            name='pinned_repository',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.weblink', verbose_name='code repository chosen'),
        ),
        migrations.AddField(
            model_name='cv',
            name='pinned_social',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.weblink', verbose_name='social profile chosen'),
        ),
        migrations.AddField(
            model_name='cv',
            name='pinned_website',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.weblink', verbose_name='website chosen'),
        ),
        migrations.AddField(
            model_name='cv',
            name='repository_choice',
            field=models.CharField(choices=[('default', 'Follow your details'), ('chosen', 'Chosen for this CV'), ('none', 'None')], default='default', max_length=10, verbose_name='code repository printed'),
        ),
        migrations.AddField(
            model_name='cv',
            name='show_form_of_address',
            field=models.BooleanField(default=False, verbose_name='print your form of address before your name'),
        ),
        migrations.AddField(
            model_name='cv',
            name='show_location',
            field=models.BooleanField(default=True, help_text='The town and country from your details, never a street.', verbose_name='print where you are'),
        ),
        migrations.AddField(
            model_name='cv',
            name='show_pronouns',
            field=models.BooleanField(default=False, verbose_name='print your pronouns after your name'),
        ),
        migrations.AddField(
            model_name='cv',
            name='social_choice',
            field=models.CharField(choices=[('default', 'Follow your details'), ('chosen', 'Chosen for this CV'), ('none', 'None')], default='default', max_length=10, verbose_name='social profile printed'),
        ),
        migrations.AddField(
            model_name='cv',
            name='website_choice',
            field=models.CharField(choices=[('default', 'Follow your details'), ('chosen', 'Chosen for this CV'), ('none', 'None')], default='default', max_length=10, verbose_name='website printed'),
        ),
        migrations.AlterField(
            model_name='cv',
            name='show_contact_details',
            field=models.BooleanField(default=True, help_text='Your name and the details chosen below, taken from your details. Unticked, this CV prints none of them.', verbose_name='include contact details'),
        ),
    ]
