"""System checks for the ESCO plugin: the data directory (#535, #1110).

The loader refuses to read a directory holding two revisions of either file, and the refusal
used to surface at the first listing saved. `manage.py check`, which the entrypoint runs
before gunicorn starts, names the state instead, and says where the occupations are still
the file of an earlier release (#1110). They moved here with the classification (#1252) and
are registered when the plugins app is ready.
"""

from __future__ import annotations

from django.core.checks import Error, Warning, register

from . import classification as esco


@register("postulo")
def esco_one_revision(app_configs, **kwargs):
    """Two occupations files or two skills files: one revision is read, never two.

    The occupations are `esco-occupations-*.zip`, or, where there is none, the `esco-*.json`
    a release before #1110 downloaded, which is the order `esco.data_file` looks in.
    """
    errors = []
    occupations = sorted(esco.DATA_DIR.glob(esco.OCCUPATIONS_PATTERN)) or sorted(
        esco.DATA_DIR.glob(esco.LEGACY_PATTERN)
    )
    for files, what, hint_id in (
        (occupations, "classifications", "postulo.E040"),
        (sorted(esco.DATA_DIR.glob(esco.SKILLS_PATTERN)), "skills files", "postulo.E041"),
    ):
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


@register("postulo")
def esco_earlier_file(app_configs, **kwargs):
    """The occupations as a release before #1110 downloaded them: read, and worth replacing.

    That file is one document of every name in all 28 languages. It is still read, so an
    upgrade changes nothing a person sees, but each process parses all of it for each
    language somebody reads in; the file `fetch_esco` writes now is read a language at a
    time. A warning and not an error: nothing is wrong, and the start-up goes on.
    """
    try:
        path = esco.data_file()
    except RuntimeError:
        # Two revisions, which `esco_one_revision` has just said.
        return []
    if not esco.is_legacy(path):
        return []
    revision = path.stem.removeprefix("esco-")
    return [
        Warning(
            f"The ESCO classification in {esco.DATA_DIR} is {path.name}, the file an earlier "
            "release of Postulo downloaded. It is still read, whole for each language.",
            hint=f"Run 'manage.py fetch_esco --revision {revision}' to replace it with the "
            "file this release reads a language at a time.",
            id="postulo.W004",
        )
    ]
