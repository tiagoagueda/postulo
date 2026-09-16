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

from postulo.accounts.models import Profile
from postulo.documents.models import RenderedDocument, UploadedDocument
from postulo.jobs.models import Company


def _referenced() -> set[str]:
    """Every file name a row points at, as stored — the media root's own relative paths."""
    names: set[str] = set()
    for model, field in (
        (UploadedDocument, "file"),
        (RenderedDocument, "file"),
        (Profile, "avatar"),
        (Profile, "gravatar_image"),
        (Company, "logo"),
    ):
        names |= {
            name
            for name in model.objects.exclude(**{f"{field}": ""}).values_list(field, flat=True)
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
