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


def owner_lookup(model) -> str | None:
    """The field that says whose a row is, or nothing for a model that has none."""
    for name in ("owner", "user"):
        try:
            if model._meta.get_field(name).is_relation:
                return name
        except Exception:  # noqa: S112 - no such field is the answer
            continue
    return None
