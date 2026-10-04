"""One rule for the slug and the identity of a tag and an industry (#356).

**The name is the identity; the slug is a handle derived from it**, for a URL and for the
applications filter. Two names are the same when they differ only in capitals, accents or
the spacing between words, which is what `same_name` asks, and never because their slugs
happen to agree: ``C#`` and ``C++`` are two tags, and *Επείγον* and *Κάτι* are two tags
although an ASCII-only slug would have made every one of them ``c`` or empty.

The slug keeps its Unicode letters, is cut to its column, is never empty and is made unique
for the owner with a number (``c``, ``c-2``), so nothing that was typed can reach the
database as a constraint violation.
"""

from __future__ import annotations

import unicodedata

from django.utils.text import slugify


def collapse(name) -> str:
    """The name as typed, with its whitespace made single spaces."""
    return " ".join(str(name).split())


def fold(name) -> str:
    """What two spellings of one name have in common: no capitals, accents or extra spaces."""
    text = unicodedata.normalize("NFKD", collapse(name))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def name_key(name) -> str:
    """The key a database constraint can hold two spellings of one employer to (#546).

    Capitals and spacing only, and accents kept: ``casefold`` folds *ΟΤΕ* to *οτε* and
    *Émile* to *émile* wherever the database runs, which ``iexact`` does not do on SQLite.
    `fold` also strips accents, which is right for a hint that tells and too loose for a
    rule that refuses.
    """
    return collapse(name).casefold()


def keep_name_key(instance, kwargs) -> None:
    """Write ``name_key`` from the name, and into ``update_fields`` when only some are saved."""
    instance.name_key = name_key(instance.name)
    fields = kwargs.get("update_fields")
    if fields is not None and "name" in fields and "name_key" not in fields:
        kwargs["update_fields"] = [*fields, "name_key"]


def same_name(rows, name):
    """The first of ``rows`` whose name is ``name`` apart from capitals, accents and spacing."""
    key = fold(name)
    return next((row for row in rows if fold(row.name) == key), None) if key else None


def unique_slug(model, owner_id, name, *, fallback: str, pk=None) -> str:
    """A slug for ``name`` that no other row of the owner's uses."""
    limit = model._meta.get_field("slug").max_length
    base = slugify(name, allow_unicode=True)[:limit].strip("-") or fallback
    taken = set(
        model.objects.filter(owner_id=owner_id).exclude(pk=pk).values_list("slug", flat=True)
    )
    slug, number = base, 2
    while slug in taken:
        suffix = f"-{number}"
        slug, number = base[: limit - len(suffix)] + suffix, number + 1
    return slug


def renamed(instance) -> bool:
    """Whether the stored row has another name than the instance now carries."""
    return (
        instance.pk is not None
        and type(instance).objects.filter(pk=instance.pk).exclude(name=instance.name).exists()
    )


def save_with_slug(instance, fallback: str, args, kwargs, save) -> None:
    """Give ``instance`` its slug when it has none or has been renamed, then save it."""
    if not instance.slug or renamed(instance):
        instance.slug = unique_slug(
            type(instance), instance.owner_id, instance.name, fallback=fallback, pk=instance.pk
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "slug"}
    save(*args, **kwargs)


def named(model, owner, names, *, fallback: str, **made) -> list:
    """The owner's rows with these names, made if missing, in the order given."""
    limit = model._meta.get_field("name").max_length
    known = list(model.objects.filter(owner=owner))
    found, seen = [], set()
    for raw in names:
        name = collapse(raw)[:limit].strip()
        key = fold(name)
        if not key or key in seen:
            continue
        seen.add(key)
        row = same_name(known, name)
        if row is None:
            row = model.objects.create(owner=owner, name=name, **made)
            known.append(row)
        found.append(row)
    return found


def rederive(model, *, fallback: str) -> None:
    """Re-derive every slug that is not the one its name gives, for a data migration.

    A row keeps its slug when it is already the slug of its name; the others take the next
    free one, after every row that is staying has been counted as taken.
    """
    limit = model._meta.get_field("slug").max_length
    for owner_id in model.objects.values_list("owner_id", flat=True).distinct():
        rows = list(model.objects.filter(owner_id=owner_id).order_by("pk"))
        base = {
            row.pk: slugify(row.name, allow_unicode=True)[:limit].strip("-") or fallback
            for row in rows
        }
        taken = {row.slug for row in rows if row.slug == base[row.pk]}
        changing = [row for row in rows if row.slug != base[row.pk]]
        # Out of the way first: the slug one of them wants may be the one another still holds.
        for row in changing:
            model.objects.filter(pk=row.pk).update(slug=f"~{row.pk}")
        for row in changing:
            slug, number = base[row.pk], 2
            while slug in taken:
                suffix = f"-{number}"
                slug, number = base[row.pk][: limit - len(suffix)] + suffix, number + 1
            taken.add(slug)
            model.objects.filter(pk=row.pk).update(slug=slug)
