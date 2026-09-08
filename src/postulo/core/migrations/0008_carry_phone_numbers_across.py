"""Move every telephone number out of its single field and into a row of its own.

Nothing is lost and nothing is invented. Each existing number becomes one ``PhoneNumber``
marked primary, with no kind: nobody was ever asked whether that number was a mobile or a
desk line, and filling one in because it is the commoner answer would be putting a fact
into somebody's records that they never gave.

**Numbers that collide.** The instance-wide uniqueness rule arrives with this table, and
existing data was never held to it -- two contacts can perfectly well have been recorded
with one company's switchboard. Refusing to migrate would be the worst outcome, and
deleting the second number would be worse still, so a colliding row is created with its
number exactly as typed and its comparable form left empty. That is the same treatment
``phones.py`` already gives a number nobody could parse: kept, readable, dialled from the
page, and taking part in no comparison. It grandfathers the duplicates rather than
pretending they are not there.

The consequence is worth stating: the first time somebody edits one of those rows, the
form tells them the number is already recorded here. That is the rule catching up with the
data, at the one moment a person is in a position to do something about it.
"""

from django.db import migrations

from postulo.core import phones


def carry_across(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    PhoneNumber = apps.get_model("core", "PhoneNumber")
    Profile = apps.get_model("accounts", "Profile")
    Contact = apps.get_model("jobs", "Contact")

    taken: set[str] = set()
    rows = []

    def row_for(holder, content_type, owner_id):
        number = (holder.phone or "").strip()
        if not number:
            return None
        normalised = phones.normalise(number)
        if normalised and normalised in taken:
            normalised = ""
        elif normalised:
            taken.add(normalised)
        return PhoneNumber(
            owner_id=owner_id,
            content_type=content_type,
            object_id=holder.pk,
            number=number,
            normalised=normalised,
            is_primary=True,
        )

    profile_type = ContentType.objects.get_for_model(Profile)
    for profile in Profile.objects.order_by("pk").iterator():
        made = row_for(profile, profile_type, profile.user_id)
        if made is not None:
            rows.append(made)

    contact_type = ContentType.objects.get_for_model(Contact)
    for contact in Contact.objects.order_by("pk").iterator():
        made = row_for(contact, contact_type, contact.owner_id)
        if made is not None:
            rows.append(made)

    PhoneNumber.objects.bulk_create(rows, batch_size=500)


def put_back(apps, schema_editor):
    """The primary goes back into the single field, which is all that field can hold."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    PhoneNumber = apps.get_model("core", "PhoneNumber")
    Profile = apps.get_model("accounts", "Profile")
    Contact = apps.get_model("jobs", "Contact")

    for model in (Profile, Contact):
        content_type = ContentType.objects.get_for_model(model)
        numbers = {
            row.object_id: row.number
            for row in PhoneNumber.objects.filter(content_type=content_type, is_primary=True)
        }
        for holder in model.objects.filter(pk__in=numbers).iterator():
            holder.phone = numbers[holder.pk]
            holder.save(update_fields=["phone"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_phonenumber"),
        ("accounts", "0010_profile_plugins_off"),
        ("jobs", "0006_company_logo"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(carry_across, put_back)]
