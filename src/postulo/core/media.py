"""Which models keep files, and whose they are: one walk instead of three lists (#355).

Account deletion and `prune_media` each kept a hand-written list of the file fields, and each
had already missed two of them. A model that gains a file field is read here from the day it
is added, because the list is the models themselves.
"""

from __future__ import annotations

from django.apps import apps
from django.db import models


def file_fields():
    """Every `(model, field)` where a row keeps a file, an image field included."""
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if isinstance(field, models.FileField):
                yield model, field


def referenced_names() -> set[str]:
    """Every file name a row points at, as stored: the media root's own relative paths.

    Read from the models, so a model that gains a file field is covered on the day it is
    added: a list kept by hand listed every export archive as an orphan (#355), and every
    kept page before that (#256), and a removal would have deleted the lot.
    """
    names: set[str] = set()
    for model, field in file_fields():
        names |= {
            name
            for name in model._base_manager.exclude(**{field.name: ""}).values_list(
                field.name, flat=True
            )
            if name
        }
    return names


def unreferenced(*, older_than=None):
    """The stored files no row names, each as `filestore.Stored`, in name order.

    ``older_than`` is a moment: a file written since is left out, because a request may be
    about to attach it. A file whose age the store cannot tell is left out too (#663).
    """
    from postulo.documents import filestore

    referenced = referenced_names()
    for stored in filestore.listing():
        if stored.name in referenced:
            continue
        if older_than is not None and (stored.modified is None or stored.modified >= older_than):
            continue
        yield stored


def owner_lookup(model) -> str | None:
    """The field that says whose a row is, or nothing for a model that has none."""
    for name in ("owner", "user"):
        try:
            if model._meta.get_field(name).is_relation:
                return name
        except Exception:  # noqa: S112 - no such field is the answer
            continue
    return None
