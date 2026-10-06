"""Which entry of the career a file is the proof of (#669).

A diploma, its supplement or a certificate belongs to an `Education` or a `Certification`
entry. The link is a generic one on the document's side, as a `CVItem`'s is, so a later
section (an accolade, #693 to #697) is one line in `PROVABLE` and no column.

A value is written `model:id` (`education:12`) wherever it crosses a form, a URL or an
archive, because a content type is this instance's number for a model and means nothing
anywhere else.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.contenttypes.models import ContentType

#: The models an entry can be proven on, by the name an archive and a form call them.
PROVABLE = {
    "education": "resume.Education",
    "certification": "resume.Certification",
}


def model_for(name: str):
    path = PROVABLE.get(name)
    return apps.get_model(path) if path else None


def name_of(entry) -> str:
    """The name `PROVABLE` knows an entry's model by, or empty for one it does not."""
    label = entry._meta.label
    return next((name for name, path in PROVABLE.items() if path == label), "")


def key_of(entry) -> str:
    return f"{name_of(entry)}:{entry.pk}"


def parse(value) -> tuple[str, int] | None:
    name, _sep, raw = str(value or "").partition(":")
    if name in PROVABLE and raw.isdecimal() and len(raw) < 10:
        return name, int(raw)
    return None


def find(user, value):
    """The entry a `model:id` names, if it is one of this person's own; otherwise None."""
    parsed = parse(value)
    if parsed is None:
        return None
    return model_for(parsed[0]).objects.for_user(user).filter(pk=parsed[1]).first()


def entries_for(user) -> list:
    """Every entry this person has that a file can prove, qualifications first."""
    return [entry for name in PROVABLE for entry in model_for(name).objects.for_user(user)]


def label_of(entry) -> str:
    """What the entry says, for a list of choices: "BSc Computer Science, University of Aveiro"."""
    if name_of(entry) == "education":
        return f"{entry.qualification}, {entry.institution}"
    return f"{entry.name}, {entry.issuer}" if entry.issuer else entry.name


def is_own(upload) -> bool:
    """Whether the entry a file points at exists, can be proven, and has the file's owner."""
    model = upload.proves_type.model_class() if upload.proves_type_id else None
    if model is None or model._meta.label not in PROVABLE.values() or upload.proves_id is None:
        return False
    return model.objects.filter(pk=upload.proves_id, owner_id=upload.owner_id).exists()


def set_proof(upload, entry) -> None:
    """Point a file at an entry, or at none."""
    if entry is None:
        upload.proves_type = None
        upload.proves_id = None
    else:
        upload.proves_type = ContentType.objects.get_for_model(entry)
        upload.proves_id = entry.pk


def counts_for(entries) -> dict:
    """How many files prove each of these entries, keyed by the entry, in one query per model."""
    from .models import UploadedDocument

    entries = list(entries)
    found: dict = {}
    for model in {type(entry) for entry in entries}:
        if model._meta.label not in PROVABLE.values():
            continue
        rows = (
            UploadedDocument.objects.filter(
                proves_type=ContentType.objects.get_for_model(model),
                proves_id__in=[e.pk for e in entries if type(e) is model],
            )
            .order_by()
            .values_list("proves_id", flat=True)
        )
        for pk in rows:
            found[(model, pk)] = found.get((model, pk), 0) + 1
    return {e: found[(type(e), e.pk)] for e in entries if (type(e), e.pk) in found}
