"""A company's location becomes its primary address's town and country (#702, #329).

Each company with a location gets a *Work* address holding its town and, where the city
table or the text names one, its country; the point it had keeps, and so does how it was
placed: a guess from the city table is placed by its town, a person's correction by hand.
A company that already lists the same address is left as it is, and one that has a primary
address keeps it, the new one beside it. Then the six columns go. Nothing is looked up
anywhere but the offline city table this instance already has.
"""

from django.db import migrations
from django.utils import timezone


def _comparable(street, postcode, town, region, country) -> str:
    parts = (street, postcode, town, region, country)
    folded = " ".join(" ".join(str(part or "").split()) for part in parts)
    return folded.casefold().strip()[:300]


def _place_key(postcode, town, country) -> str:
    parts = (postcode, town, country)
    return "|".join(" ".join(str(part or "").split()).casefold() for part in parts)[:300]


def into_addresses(apps, schema_editor):
    from postulo.jobs.models import split_location

    Company = apps.get_model("jobs", "Company")
    PostalAddress = apps.get_model("core", "PostalAddress")
    ContentType = apps.get_model("contenttypes", "ContentType")
    company_type, _made = ContentType.objects.get_or_create(app_label="jobs", model="company")
    for company in Company.objects.exclude(location="").iterator():
        town, country = split_location(company.location)
        if not town:
            continue
        held = PostalAddress.objects.filter(content_type=company_type, object_id=company.pk)
        comparable = _comparable("", "", town, "", country)
        if held.filter(comparable=comparable).exists():
            continue
        placed_by = ""
        if company.location_lat is not None and company.location_lon is not None:
            placed_by = "manual" if company.location_resolved_by == "manual" else "town"
        PostalAddress.objects.create(
            owner_id=company.owner_id,
            content_type=company_type,
            object_id=company.pk,
            kind="work",
            municipality=town,
            country=country,
            is_primary=not held.filter(is_primary=True).exists(),
            comparable=comparable,
            latitude=company.location_lat if placed_by else None,
            longitude=company.location_lon if placed_by else None,
            placed_by=placed_by,
            placed_from=_place_key("", town, country) if placed_by else "",
            placed_at=company.location_resolved_at or (timezone.now() if placed_by else None),
            created_at=timezone.now(),
            updated_at=timezone.now(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0032_company_email'),
        ('core', '0036_address_on_the_map'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(into_addresses, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='company',
            name='location',
        ),
        migrations.RemoveField(
            model_name='company',
            name='location_lat',
        ),
        migrations.RemoveField(
            model_name='company',
            name='location_lon',
        ),
        migrations.RemoveField(
            model_name='company',
            name='location_resolved_at',
        ),
        migrations.RemoveField(
            model_name='company',
            name='location_resolved_by',
        ),
        migrations.RemoveField(
            model_name='company',
            name='location_resolved_from',
        ),
    ]
