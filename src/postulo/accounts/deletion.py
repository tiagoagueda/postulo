"""Deleting an account: everything, including the files on disk.

Rows already cascade — every owned model hangs off its owner with ``CASCADE``, and so do
allauth's addresses, the API tokens, the connections and their secrets. Files do not:
Django never removes a file when the row pointing at it goes, so a deletion that stopped
at the database would leave a person's CV on the server, readable by nobody and deleted by
nobody. That is not a deletion.

So the order here is deliberate: collect the names of every file the person's rows point
at, delete the account, and remove the files only once the transaction that deleted it
commits (``transaction.on_commit``). Inside a request that transaction is the request's own
(``ATOMIC_REQUESTS``), so a request that fails after the deletion rolls the rows back and
leaves the files exactly where they were, and a file that fails to delete costs that file,
not the account (#355).

One rule sits above all of it: the last administrator of an instance cannot be deleted,
by anyone, including themselves. Somebody has to be able to open the door.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db import transaction

from postulo.documents import filestore


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


#: The folders under the media root that are kept per person, one folder each.
MEDIA_FOLDERS = ("documents", "avatars", "captures", "exports", "logos")


def files_of(user) -> list[str]:
    """Every storage name the person's rows point at.

    Read from the models rather than from a list: every file field of every model whose rows
    are the person's, so a model that gains one is covered the day it is added (#355). That
    includes what a capture kept of a page (#256), the export archive and the company
    logos, none of which a hand-written list remembered.
    """
    from postulo.core import media

    names: list[str] = []
    for model, column in media.file_fields():
        owner = media.owner_lookup(model)
        if owner is None:
            continue
        rows = model._base_manager.filter(**{owner: user}).exclude(**{column.name: ""})
        names.extend(rows.values_list(column.name, flat=True))
    return [name for name in names if name]


def media_directories_of(user) -> list[str]:
    """The per-person folders files were stored under, to prune once empty."""
    return [f"{folder}/{user.pk}" for folder in MEDIA_FOLDERS]


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

    # What the report says is worked out now, from what is on disk, because the callers read
    # it straight away; the removal itself waits for the commit.
    present = []
    for name in names:
        if filestore.exists(name):
            present.append(name)
        else:
            report.files_missing += 1
    report.files_removed = len(present)
    report.directories_removed = sum(filestore.would_prune(d, set(present)) for d in directories)

    def remove_files() -> None:
        for name in names:
            try:
                if filestore.exists(name):
                    filestore.delete(name)
            except OSError:
                pass
        for directory in directories:
            filestore.prune_empty_directories(directory)

    # Registered after ``user.delete()``, so it runs after the receivers that remove files
    # of their own, and only if the deletion really commits.
    transaction.on_commit(remove_files)
    return report
