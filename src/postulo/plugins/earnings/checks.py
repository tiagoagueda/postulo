"""System checks for the earnings plugin: a refreshed file that cannot be read (#1257).

The reader passes over a file in `POSTULO_EARNINGS_DIR` that is not one it can use and answers
from the shipped one, so nothing breaks; but an administrator who ran ``fetch_earnings`` would
believe they were showing the newer figures. `manage.py check`, which the entrypoint runs,
says so. Registered when the plugins app is ready.
"""

from __future__ import annotations

from django.core.checks import Warning, register

from . import reader


@register("postulo")
def earnings_file_readable(app_configs, **kwargs):
    """Each file the reader may read is one it can, and at least one is."""
    found = reader.candidates()
    warnings = [
        Warning(
            f"{path} is not an earnings file Postulo can read; the figures shown are from "
            "the file that ships with Postulo.",
            hint="Run manage.py fetch_earnings again, or delete the file.",
            id="postulo.W050",
        )
        for path, read in found
        if read is None
    ]
    if not any(read is not None for _path, read in found):
        warnings.append(
            Warning(
                "No earnings file can be read, so the earnings plugin shows nothing.",
                hint="Run manage.py fetch_earnings, or reinstall Postulo's package data.",
                id="postulo.W051",
            )
        )
    return warnings
