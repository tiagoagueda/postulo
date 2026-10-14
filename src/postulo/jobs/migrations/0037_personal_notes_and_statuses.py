"""A person's own note, listing status and listing history, off the shared rows (#896)."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


BATCH = 1000


def to_their_owners(apps, schema_editor):
    """Each note, status and history entry becomes the record owner's own (#896).

    Before this, a note and a status were columns on the record, and the record had one
    owner; a group-owned record could not exist yet. So the owner is who wrote them.
    Every listing gets a status, not only a decided one, so that `noted_at` -- when it
    entered the person's listings -- is kept exactly rather than read back as its creation.
    """
    Company = apps.get_model("jobs", "Company")
    Contact = apps.get_model("jobs", "Contact")
    JobPosting = apps.get_model("jobs", "JobPosting")
    CompanyNote = apps.get_model("jobs", "CompanyNote")
    ContactNote = apps.get_model("jobs", "ContactNote")
    ListingStatus = apps.get_model("jobs", "ListingStatus")
    ListingEvent = apps.get_model("jobs", "ListingEvent")

    CompanyNote.objects.bulk_create(
        (
            CompanyNote(owner_id=owner, company_id=pk, text=notes)
            for pk, owner, notes in Company.objects.exclude(notes="")
            .filter(owner__isnull=False)
            .values_list("pk", "owner_id", "notes")
            .iterator()
        ),
        batch_size=BATCH,
    )
    ContactNote.objects.bulk_create(
        (
            ContactNote(owner_id=owner, contact_id=pk, text=notes)
            for pk, owner, notes in Contact.objects.exclude(notes="")
            .filter(owner__isnull=False)
            .values_list("pk", "owner_id", "notes")
            .iterator()
        ),
        batch_size=BATCH,
    )
    ListingStatus.objects.bulk_create(
        (
            ListingStatus(
                owner_id=row["owner_id"],
                posting_id=row["pk"],
                state=row["state"],
                discard_reason=row["discard_reason"],
                noted_at=row["noted_at"],
                decided_at=row["decided_at"],
            )
            for row in JobPosting.objects.filter(owner__isnull=False)
            .values("pk", "owner_id", "state", "discard_reason", "noted_at", "decided_at")
            .iterator()
        ),
        batch_size=BATCH,
    )
    for posting_id, owner_id in (
        JobPosting.objects.filter(events__isnull=False).values_list("pk", "owner_id").distinct()
    ):
        ListingEvent.objects.filter(posting_id=posting_id).update(owner_id=owner_id)


def back_onto_the_rows(apps, schema_editor):
    """The owner's own note and status go back onto the record; anybody else's are lost.

    Nothing but the owner's could exist before this migration, so a database that never
    shared anything comes back exactly as it was.
    """
    Company = apps.get_model("jobs", "Company")
    Contact = apps.get_model("jobs", "Contact")
    JobPosting = apps.get_model("jobs", "JobPosting")
    CompanyNote = apps.get_model("jobs", "CompanyNote")
    ContactNote = apps.get_model("jobs", "ContactNote")
    ListingStatus = apps.get_model("jobs", "ListingStatus")

    for note in CompanyNote.objects.iterator():
        Company.objects.filter(pk=note.company_id, owner_id=note.owner_id).update(notes=note.text)
    for note in ContactNote.objects.iterator():
        Contact.objects.filter(pk=note.contact_id, owner_id=note.owner_id).update(notes=note.text)
    for status in ListingStatus.objects.iterator():
        JobPosting.objects.filter(pk=status.posting_id, owner_id=status.owner_id).update(
            state=status.state,
            discard_reason=status.discard_reason,
            noted_at=status.noted_at,
            decided_at=status.decided_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('jobs', '0036_principal_owners_and_shares'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('text', models.TextField(verbose_name='notes')),
            ],
            options={
                'verbose_name': 'note on a company',
                'verbose_name_plural': 'notes on companies',
            },
        ),
        migrations.CreateModel(
            name='ContactNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('text', models.TextField(verbose_name='notes')),
            ],
            options={
                'verbose_name': 'note on a contact',
                'verbose_name_plural': 'notes on contacts',
            },
        ),
        migrations.CreateModel(
            name='ListingStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('state', models.CharField(choices=[('new', 'New'), ('shortlisted', 'Shortlisted'), ('discarded', 'Discarded')], default='new', max_length=20, verbose_name='state')),
                ('discard_reason', models.CharField(blank=True, choices=[('not_for_me', 'Not for me'), ('pay', 'The pay'), ('location', 'The location'), ('closed', 'Closed or already filled'), ('other', 'Other')], max_length=20, verbose_name='why it was discarded')),
                ('noted_at', models.DateTimeField(default=django.utils.timezone.now, help_text='When it entered your listings.', verbose_name='noted on')),
                ('decided_at', models.DateTimeField(blank=True, null=True, verbose_name='decided on')),
            ],
            options={
                'verbose_name': 'listing status',
                'verbose_name_plural': 'listing statuses',
            },
        ),
        migrations.AddField(
            model_name='listingevent',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='listing_events', to=settings.AUTH_USER_MODEL, verbose_name='owner'),
        ),
        migrations.AddField(
            model_name='companynote',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personal_notes', to='jobs.company', verbose_name='company'),
        ),
        migrations.AddField(
            model_name='companynote',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to=settings.AUTH_USER_MODEL, verbose_name='owner'),
        ),
        migrations.AddField(
            model_name='contactnote',
            name='contact',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personal_notes', to='jobs.contact', verbose_name='contact'),
        ),
        migrations.AddField(
            model_name='contactnote',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to=settings.AUTH_USER_MODEL, verbose_name='owner'),
        ),
        migrations.AddField(
            model_name='listingstatus',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to=settings.AUTH_USER_MODEL, verbose_name='owner'),
        ),
        migrations.AddField(
            model_name='listingstatus',
            name='posting',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statuses', to='jobs.jobposting', verbose_name='listing'),
        ),
        migrations.AddConstraint(
            model_name='companynote',
            constraint=models.UniqueConstraint(fields=('owner', 'company'), name='one_note_per_company_each'),
        ),
        migrations.AddConstraint(
            model_name='contactnote',
            constraint=models.UniqueConstraint(fields=('owner', 'contact'), name='one_note_per_contact_each'),
        ),
        migrations.AddIndex(
            model_name='listingstatus',
            index=models.Index(fields=['owner', 'state'], name='jobs_listin_owner_i_2f9918_idx'),
        ),
        migrations.AddConstraint(
            model_name='listingstatus',
            constraint=models.UniqueConstraint(fields=('owner', 'posting'), name='one_status_per_listing_each'),
        ),
        # Every note, status and history entry goes to the record's owner (#896).
        migrations.RunPython(to_their_owners, back_onto_the_rows),
        migrations.AlterField(
            model_name="listingevent",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="listing_events",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.RemoveConstraint(
            model_name='listingevent',
            name='listing_event_once_per_source_id',
        ),
        migrations.AlterModelOptions(
            name='jobposting',
            options={'ordering': ('-created_at', '-pk'), 'verbose_name': 'job posting', 'verbose_name_plural': 'job postings'},
        ),
        migrations.RemoveIndex(
            model_name='jobposting',
            name='jobs_jobpos_owner_i_a7eca7_idx',
        ),
        migrations.RemoveField(
            model_name='company',
            name='notes',
        ),
        migrations.RemoveField(
            model_name='contact',
            name='notes',
        ),
        migrations.RemoveField(
            model_name='jobposting',
            name='decided_at',
        ),
        migrations.RemoveField(
            model_name='jobposting',
            name='discard_reason',
        ),
        migrations.RemoveField(
            model_name='jobposting',
            name='noted_at',
        ),
        migrations.RemoveField(
            model_name='jobposting',
            name='state',
        ),
        migrations.AddConstraint(
            model_name='listingevent',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('posting', 'owner', 'external_id'), name='listing_event_once_per_source_id'),
        ),
    ]
