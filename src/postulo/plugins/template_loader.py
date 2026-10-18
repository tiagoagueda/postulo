"""The loader that reads an installed plugin's `templates/`, and reads it last (#971).

Django's filesystem loader walks ``DIRS`` before the app-directories loader, so a directory
added there is searched ahead of every template an installed app ships: a plugin's
``django/forms/widgets/input.html`` would redraw every input on the instance. This loader
comes after both, so whatever Postulo or an app has by a name is what is used, and a plugin's
file answers only for a name nothing else has: its own theme's templates.
"""

from __future__ import annotations

from pathlib import Path

from django.template.loaders.filesystem import Loader as FilesystemLoader


class Loader(FilesystemLoader):
    """The filesystem loader, over the directories plugins registered (see `themes`)."""

    def get_dirs(self):
        from . import themes

        return [Path(one) for one in themes._registered]
