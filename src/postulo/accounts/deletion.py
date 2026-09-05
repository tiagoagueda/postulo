"""Deleting an account: everything, including the files on disk.

Rows already cascade — every owned model hangs off its owner with ``CASCADE``, and so do
allauth's addresses, the API tokens, the connections and their secrets. Files do not:
Django never removes a file when the row pointing at it goes, so a deletion that stopped
at the database would leave a person's CV on the server, readable by nobody and deleted by
nobody. That is not a deletion.

So the order here is deliberate: collect the names of every file the person's rows point
at, delete the account in one transaction, and only then remove the files. A transaction
that fails leaves the files exactly where they were, and a file that fails to delete costs
that file, not the account.

One rule sits above all of it: the last administrator of an instance cannot be deleted,
by anyone, including themselves. Somebody has to be able to open the door.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction


class LastAdministrator(Exception):
    """Raised when the account to delete is the only active administrator left."""


@dataclass
class DeletionReport:
    username: str
    email: str
    rows: dict[str, int] = field(default_factory=dict)
    files_removed: int = 0
    files_missing: int = 0
    directories_removed: int = 0

    def as_lines(self) -> list[str]:
        lines = [f"Deleted the account {self.username} <{self.email}>."]
        for name, count in self.rows.items():
            if count:
                lines.append(f"  {count} {name}")
        lines.append(f"  {self.files_removed} files removed from disk")
        if self.files_missing:
            lines.append(f"  {self.files_missing} files were already gone")
        return lines


def is_last_administrator(user) -> bool:
    """Whether ``user`` is the only active administrator left on the instance."""
    if not (user.is_staff and user.is_active):
        return False
    others = get_user_model().objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk)
    return not others.exists()


def files_of(user) -> list[str]:
    """Every storage name the person's rows point at."""
    from postulo.accounts.models import Profile
    from postulo.documents.models import RenderedDocument, UploadedDocument

    names: list[str] = []
    for model in (UploadedDocument, RenderedDocument):
        for record in model.objects.for_user(user).exclude(file=""):
            names.append(record.file.name)
    profile = Profile.objects.filter(user=user).first()
    if profile is not None:
        for picture in (profile.avatar, profile.gravatar_image):
            if picture:
                names.append(picture.name)
    return names


def media_directories_of(user) -> list[Path]:
    """The per-person directories files were stored under, to prune once empty."""
    root = Path(settings.MEDIA_ROOT)
    return [root / "documents" / str(user.pk), root / "avatars" / str(user.pk)]


def delete_account(user) -> DeletionReport:
    """Delete ``user`` and everything they own, then the files behind it.

    Raises :class:`LastAdministrator` rather than leaving an instance nobody can
    administer. Pending invitations the person issued are revoked with them.
    """
    if is_last_administrator(user):
        raise LastAdministrator(
            f"{user.username} is the last administrator. Appoint another before deleting."
        )

    from postulo.accounts.models import Invite

    report = DeletionReport(username=user.username, email=user.email)
    names = files_of(user)
    directories = media_directories_of(user)

    with transaction.atomic():
        report.rows["pending invitations revoked"] = (
            Invite.objects.filter(created_by=user).pending().delete()[0]
        )
        _count, per_model = user.delete()
        for label, count in per_model.items():
            report.rows[label.split(".")[-1].lower()] = count

    for name in names:
        try:
            if default_storage.exists(name):
                default_storage.delete(name)
                report.files_removed += 1
            else:
                report.files_missing += 1
        except OSError:
            report.files_missing += 1

    for directory in directories:
        report.directories_removed += _prune_empty(directory)
    return report


def _prune_empty(directory: Path) -> int:
    """Remove ``directory`` and any empty directories inside it. Never a file."""
    removed = 0
    if not directory.is_dir():
        return removed
    for child in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            try:
                child.rmdir()
                removed += 1
            except OSError:
                pass
    try:
        directory.rmdir()
        removed += 1
    except OSError:
        pass
    return removed
