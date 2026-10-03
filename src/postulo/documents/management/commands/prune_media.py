"""Find files under the media root that no row points at, and optionally remove them (#217).

Deleting a document removes its file from now on, but every document deleted before that
left its bytes behind, and a restore from an older archive can bring more. This is how an
operator finds them and decides.

It lists by default and removes only when told to, because the failure mode of the opposite
default is somebody's CV.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from postulo.core import media


def _referenced() -> set[str]:
    """Every file name a row points at, as stored — the media root's own relative paths.

    Read from the models, so a model that gains a file field is covered on the day it is
    added: a list kept by hand listed every export archive as an orphan (#355), and every
    kept page before that (#256), and `--remove` would have deleted the lot.
    """
    names: set[str] = set()
    for model, field in media.file_fields():
        names |= {
            name
            for name in model._base_manager.exclude(**{field.name: ""}).values_list(
                field.name, flat=True
            )
            if name
        }
    return names


class Command(BaseCommand):
    help = "List files under MEDIA_ROOT that no record points at; --remove deletes them."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the files as well as listing them. Without it, nothing is touched.",
        )

    def handle(self, *args, **options) -> None:
        root = Path(settings.MEDIA_ROOT)
        if not root.is_dir():
            self.stdout.write(f"{root} is not a directory; nothing to look at.")
            return

        referenced = _referenced()
        orphans: list[Path] = []
        kept = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in referenced:
                kept += 1
                continue
            orphans.append(path)

        total = sum(path.stat().st_size for path in orphans)
        for path in orphans:
            self.stdout.write(f"orphan  {path.relative_to(root).as_posix()}")
        self.stdout.write(
            f"{kept} file(s) in use, {len(orphans)} with no record ({total / 1024:.0f} KB)."
        )
        if not options["remove"]:
            if orphans:
                self.stdout.write("Nothing was deleted. Pass --remove to delete them.")
            return

        for path in orphans:
            path.unlink(missing_ok=True)
        self.stdout.write(self.style.SUCCESS(f"Removed {len(orphans)} file(s)."))
