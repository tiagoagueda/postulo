"""A system check for the ESCO data directory (#535).

The loader refuses to read a directory holding two revisions of either file, and the refusal
used to surface at the first listing saved. `manage.py check`, which the entrypoint runs
before gunicorn starts, names the state instead.
"""

from __future__ import annotations

from django.core.checks import Error, register

from postulo.jobs import esco


@register("postulo")
def esco_one_revision(app_configs, **kwargs):
    """Two `esco-*.json` or two `esco-skills-*.zip` files: one revision is read, never two."""
    errors = []
    for pattern, what, hint_id in (
        ("esco-*.json", "classifications", "postulo.E040"),
        ("esco-skills-*.zip", "skills files", "postulo.E041"),
    ):
        files = sorted(esco.DATA_DIR.glob(pattern))
        if len(files) > 1:
            names = ", ".join(file.name for file in files)
            errors.append(
                Error(
                    f"Two ESCO {what} in {esco.DATA_DIR}: {names}.",
                    hint="Delete the revision being replaced; Postulo reads one at a time.",
                    id=hint_id,
                )
            )
    return errors
