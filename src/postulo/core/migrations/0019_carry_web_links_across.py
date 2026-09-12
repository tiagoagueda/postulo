"""Move every web address out of its single column and into a row of its own.

Nothing is lost and nothing is invented. A profile's website, LinkedIn and code repository
and a contact's LinkedIn each become one ``WebLink`` of the kind the column was for, marked
primary, with no name: nobody was ever asked what to call it, and the host is what the page
shows for a row with no name, which is what the column showed too (#189).

A LinkedIn address becomes a *social profile* rather than a row called LinkedIn, because
the kind describes the sort of thing and never the host; the address says which host it is.
"""

from django.db import migrations

#: Column on the holder, and the kind its value becomes.
PROFILE_COLUMNS = (
    ("website", "website"),
    ("linkedin_url", "social"),
    ("source_repo_url", "repository"),
)
CONTACT_COLUMNS = (("linkedin_url", "social"),)


def carry_across(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    WebLink = apps.get_model("core", "WebLink")
    Profile = apps.get_model("accounts", "Profile")
    Contact = apps.get_model("jobs", "Contact")

    rows = []

    def rows_for(holder, content_type, owner_id, columns):
        seen = set()
        for column, kind in columns:
            url = (getattr(holder, column) or "").strip()
            # The same address typed in two boxes is one row: it is unique per holder.
            if not url or url in seen:
                continue
            seen.add(url)
            rows.append(
                WebLink(
                    owner_id=owner_id,
                    content_type=content_type,
                    object_id=holder.pk,
                    kind=kind,
                    url=url,
                    is_primary=True,
                )
            )

    profile_type = ContentType.objects.get_for_model(Profile)
    for profile in Profile.objects.order_by("pk").iterator():
        rows_for(profile, profile_type, profile.user_id, PROFILE_COLUMNS)

    contact_type = ContentType.objects.get_for_model(Contact)
    for contact in Contact.objects.order_by("pk").iterator():
        rows_for(contact, contact_type, contact.owner_id, CONTACT_COLUMNS)

    WebLink.objects.bulk_create(rows, batch_size=500)


def put_back(apps, schema_editor):
    """The primary of each kind goes back into its column, which is all a column can hold."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    WebLink = apps.get_model("core", "WebLink")
    Profile = apps.get_model("accounts", "Profile")
    Contact = apps.get_model("jobs", "Contact")

    for model, columns in ((Profile, PROFILE_COLUMNS), (Contact, CONTACT_COLUMNS)):
        content_type = ContentType.objects.get_for_model(model)
        by_kind = {kind: column for column, kind in columns}
        found = {}
        for row in WebLink.objects.filter(content_type=content_type, is_primary=True):
            if row.kind in by_kind:
                found.setdefault(row.object_id, {})[by_kind[row.kind]] = row.url
        for holder in model.objects.filter(pk__in=found).iterator():
            for column, url in found[holder.pk].items():
                setattr(holder, column, url[:200])
            holder.save(update_fields=list(found[holder.pk]))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_weblink"),
        ("accounts", "0015_owned_dashboard"),
        ("jobs", "0011_industry_nace"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(carry_across, put_back)]
