"""Why an application ended, who referred the person, and through which agency (#239).

Three additive columns, and nothing carried across because nothing was there to carry.

`end_reason` is on the **timeline entry**, not on the application: the entry that says an
application ended is where the reason belongs, so the log goes on being the account of
what happened and an application that ends twice keeps both reasons. Every existing entry
gets the empty string, which is what an ending nobody explained has always been.

`referred_by` and `through_agency` are on the application, both optional and both
`SET_NULL`: a contact or a company going away must not take the attempt with it. The
posting's company is untouched and stays the employer.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0013_offers'),
        ('jobs', '0018_company_location_coordinates'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='referred_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referrals', to='jobs.contact', verbose_name='referred by'),
        ),
        migrations.AddField(
            model_name='application',
            name='through_agency',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='placements', to='jobs.company', verbose_name='through agency'),
        ),
        migrations.AddField(
            model_name='applicationevent',
            name='end_reason',
            field=models.CharField(blank=True, choices=[('pay', 'The pay'), ('location', 'The location'), ('not_a_match', 'Not a match'), ('filled_internally', 'Filled internally'), ('my_choice', 'My own choice'), ('other', 'Other')], max_length=20, verbose_name='why it ended'),
        ),
    ]
