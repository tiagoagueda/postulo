"""The surface check, for a plugin's own test suite to run against itself (#229).

`postulo.plugins.api` is a promise: those names keep working across a minor release, and
everything else in `postulo` is this month's internals. Postulo holds itself to it —
`tests/test_plugin_surface.py` walks every plugin it ships and fails on one that reaches
past — and until now a plugin written outside the core had no way to run the same check.
So each of the official ones drifted past the surface without anything saying so, and the
drift was found by a code audit rather than by a test.

This module is that check, published the way the catalogue tool was (#187): a plugin
repository already depends on Postulo for its tests, so it can import this and assert the
same thing about itself in three lines.

    from pathlib import Path

    from postulo.plugins.testing import assert_imports_only_the_surface

    def test_the_plugin_imports_only_the_surface():
        assert_imports_only_the_surface(
            Path(__file__).parent.parent / "src" / "postulo_yours",
            package="postulo_yours",
        )

**It reads the source rather than importing it**, so a module that reaches past the surface
fails whether or not the import is ever executed: a lazy ``from postulo.core import site``
inside a method is exactly as much of a dependency as one at the top of the file, and is the
shape most real ones take. Relative imports are resolved, because a dependency spelled with
a dot is still a dependency.

**`allowed` is not a list of exemptions.** It maps a module to the reason that plugin has to
reach for it, and a new entry is a decision somebody makes on purpose. A stale entry is a
failure too — see `unused_allowances` — because one nobody removed hides the next real
dependency behind it.

**And a backup destination held to its contract (#325).** `assert_is_a_backup_destination`
sends one small archive through a destination, lists it and deletes it, which is everything
Postulo's retention relies on: a destination whose ``list`` does not return what it was sent
would never have an old archive removed.

**And what a call wrote, read back (#270).** `record_listing_event` is on the surface and the
rows it writes are not: the model is Postulo's to change. A plugin that binds things to a
listing still has to see, in its own tests, what its call wrote -- `listing_history` is
that, as plain values, so a plugin's test suite can assert on the outcome without depending
on the table underneath it.

**And pairing by hand, as the page does it (#327).** `pair_by_hand`, `unpair_by_hand` and
`create_by_hand` go through what a sync connection's *Pairing* page goes through -- the item
asked of the plugin again by its key, the address taken from that answer, the record made by
the plugin's ``fill_record`` and checked -- without a browser, so a sync plugin's own tests can
see what its next run makes of each.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

#: The one module a plugin may import from, and the reason there is exactly one.
SURFACE = "postulo.plugins.api"


def _is_postulo(name: str) -> bool:
    """Whether ``name`` is a module of Postulo's, rather than one merely named like it.

    ``postulo_dav`` starts with the same eight letters and is the plugin's own code.
    """
    return name == "postulo" or name.startswith("postulo.")


def _package_parts(path: Path, *, root: Path, package: str) -> list[str]:
    """The dotted package a file sits in, for resolving its relative imports."""
    return [*package.split("."), *path.relative_to(root).parts[:-1]]


def _within(name: str, entry: str) -> bool:
    """Whether ``name`` is ``entry`` or inside it: ``a.b.c`` is within ``a.b``, ``a.bc`` is not."""
    return name == entry or name.startswith(f"{entry}.")


def imports_in(path: Path, *, root: Path, package: str) -> set[str]:
    """Every ``postulo.*`` name one file imports, at any depth, read rather than run.

    ``from postulo.core import site`` is ``postulo.core.site``: the module and the name it
    takes, so that a name can be allowed or refused for itself and not for its package.

    ``package`` is the plugin's own dotted name: reaching into itself is not reaching past
    the surface, and a plugin that has become a package is allowed an inside.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    here = _package_parts(path, root=root, package=package)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[: len(here) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            # What is imported, not only where from: `from postulo.core import site` reaches
            # `postulo.core.site`, and an allowance for that module says no more than it reads.
            names = [
                f"{module}.{alias.name}" if alias.name != "*" else module for alias in node.names
            ]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            mine = name == package or name.startswith(f"{package}.")
            if _is_postulo(name) and not mine:
                found.add(name)
    return found


def imports_of(package_dir: Path | str, *, package: str) -> set[str]:
    """Every ``postulo.*`` name a whole plugin package imports (see `imports_in`).

    ``package_dir`` is the directory the package's ``__init__.py`` is in; ``package`` is what
    it is imported as. Every ``.py`` under it is read, because a plugin is more than its
    ``__init__``.
    """
    root = Path(package_dir)
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        found |= imports_in(path, root=root, package=package)
    return found


def reaching_past_the_surface(
    package_dir: Path | str,
    *,
    package: str,
    allowed: Mapping[str, str] | Iterable[str] = (),
) -> list[str]:
    """What this plugin imports from Postulo that is neither the surface nor written down."""
    permitted = [SURFACE, *allowed]
    return sorted(
        name
        for name in imports_of(package_dir, package=package)
        if not any(_within(name, entry) for entry in permitted)
    )


def unused_allowances(
    package_dir: Path | str,
    *,
    package: str,
    allowed: Mapping[str, str] | Iterable[str] = (),
) -> list[str]:
    """Modules ``allowed`` names that the plugin no longer imports.

    Checked as well as the other direction, because the list is the map of what is left to
    do: an entry nobody removed lets the next real dependency in behind it.
    """
    imported = imports_of(package_dir, package=package)
    return sorted(
        entry for entry in set(allowed) if not any(_within(name, entry) for name in imported)
    )


def assert_imports_only_the_surface(
    package_dir: Path | str,
    *,
    package: str,
    allowed: Mapping[str, str] | Iterable[str] = (),
) -> None:
    """Fail unless this plugin imports `SURFACE` and whatever ``allowed`` says it may.

    Both directions, in one call: something reached for that is not written down, and
    something written down that is no longer reached for.
    """
    past = reaching_past_the_surface(package_dir, package=package, allowed=allowed)
    assert not past, (
        f"{package} reaches past the plugin surface: {past}. Either use the name on "
        f"{SURFACE} instead, ask for it to be added, or record the dependency in `allowed` "
        f"with the reason it is needed — a new one is a decision, not an accident."
    )

    stale = unused_allowances(package_dir, package=package, allowed=allowed)
    assert not stale, (
        f"{package} no longer imports {stale}. Delete those entries from `allowed`: the "
        f"list is the map of what is left to do, and a stale entry hides the next one."
    )


def assert_is_a_backup_destination(plugin, config: dict, *, tmp_path: Path) -> None:
    """Hold a backup destination to its contract, against a configuration of your own (#325).

    For a destination's own test suite, pointed at something it controls -- a fake server, a
    temporary bucket, a directory. Sends one small archive the way Postulo hands one over,
    and fails unless the plugin answers to the protocol, gives back a reference with an id,
    lists that reference, and deletes it so that it is no longer listed. Retention depends on
    the last two: Postulo only ever deletes what ``list`` returned.

    ``tmp_path`` is a directory the archive is written into; pytest's fixture of that name is
    the usual one.
    """
    import datetime as dt

    from .base import BackupArchive, BackupDestinationPlugin, BackupManifest, BackupRef, FieldSpec

    assert isinstance(plugin, BackupDestinationPlugin), (
        f"{plugin!r} does not provide name, version, kind, label, config_fields, test, send, "
        "list and delete"
    )
    assert plugin.kind == "backup-destination", f"its kind is {plugin.kind!r}"
    assert all(isinstance(spec, FieldSpec) for spec in plugin.config_fields())

    payload = b"age-encryption.org/v1\n" + bytes(range(256)) * 64
    path = Path(tmp_path) / "postulo-backup-20260101-030000.tar.gz.age"
    path.write_bytes(payload)
    archive = BackupArchive(name=path.name, size=len(payload), _path=path)
    manifest = BackupManifest(
        archive="postulo-backup-20260101-030000.tar.gz",
        created_at=dt.datetime(2026, 1, 1, 3, 0, tzinfo=dt.UTC),
        version="0.0.0",
        engine="sqlite",
        instance="Postulo",
        counts=(("users", 1),),
    )

    reference = plugin.send(archive, manifest, config)
    assert isinstance(reference, BackupRef), f"send returned {reference!r}, not a BackupRef"
    assert str(reference.id), "a reference needs an id the destination can find it by"
    listed = plugin.list(config)
    assert all(isinstance(item, BackupRef) for item in listed), "list returns BackupRefs"
    found = [item for item in listed if item.id == reference.id]
    assert found, f"{reference.id!r} was sent and is not listed"
    plugin.delete(found[0], config)
    assert all(item.id != reference.id for item in plugin.list(config)), (
        f"{reference.id!r} is still listed after it was deleted"
    )


def listing_history(record) -> list[dict]:
    """A listing's history as plain values, newest first, for a plugin's own tests (#270).

    ``record`` is what `record_listing_event` takes: the listing, or an application whose
    listing it is. Each entry is its ``kind``, ``summary``, ``body``, ``occurred_at``,
    ``actor`` and ``external_id``; ``contact``, the name of whoever it came from or empty;
    and ``points_at``, ``"capture"``, ``"document"`` or empty -- the same things the page
    and the API say about it, and nothing a plugin could not have written itself.

    Read, never written: a test that wants an entry makes one through the surface.
    """
    from postulo.jobs.history import history_of, listing_of

    entries = []
    # The record's own person's history (#896): an application's owner, a listing's.
    for event in history_of(listing_of(record), record.owner):
        points_at = ""
        if event.bound_capture is not None:
            points_at = "capture"
        elif event.bound_document is not None:
            points_at = "document"
        entries.append(
            {
                "kind": event.kind,
                "summary": event.summary,
                "body": event.body,
                "occurred_at": event.occurred_at,
                "actor": event.actor,
                "external_id": event.external_id,
                "contact": event.contact.name if event.contact_id else "",
                "points_at": points_at,
            }
        )
    return entries


def _on_the_server(connection):
    """What the page's call to the plugin does, without a request: an answer or a sentence."""

    def call(method):
        try:
            return True, method(connection.full_config)
        except Exception as error:
            return False, f"{type(error).__name__}: {error}"

    return call


def pair_by_hand(connection, record, *, collection: str, uid: str):
    """Pair ``record`` with the item ``(collection, uid)`` as *Pair* on the page does (#327).

    The plugin is asked for the item by its key, and the `SyncLink` is written from its
    answer, with no version tag, for its next run to compare. Raises `AlreadyPaired` for an
    item another record holds, and `postulo.plugins.pairing.Refusal` for one the plugin no
    longer has or that cannot be paired with a record of this kind. Returns the link.
    """
    from . import pairing

    plugin = connection.plugin_instance
    item = pairing.find(_on_the_server(connection), plugin, collection, uid)
    if pairing.kind_of(record) not in item.kinds:
        raise pairing.Refusal("That item cannot be paired with one of those.")
    return pairing.pair(connection, item, record)


def unpair_by_hand(connection, record, slot: str = ""):
    """Undo ``record``'s pairing in ``slot`` as *Unpair* does: both sides kept, the row too.

    Returns the link, or ``None`` where there was none to undo.
    """
    from . import pairing
    from .models import SyncLink

    link = SyncLink.for_record(connection, record, slot)
    if link is None or not link.paired:
        return None
    return pairing.unpair(link)


def create_by_hand(connection, *, collection: str, uid: str, kind: str, into=None, pair=True):
    """Have the plugin make a ``kind`` from an item, or fill ``into``, as the page does.

    The plugin's ``fill_record`` is called with the item as it answers for it now, and what it
    returns is held to the same check: a record of ``kind``, the connection owner's own, and
    ``into`` itself when one was given. Then it is paired with the item, unless ``pair`` is
    false -- the one-off import. Returns the record.
    """
    from . import pairing

    plugin = connection.plugin_instance
    on_the_server = _on_the_server(connection)
    item = pairing.find(on_the_server, plugin, collection, uid)
    record = pairing.fill(on_the_server, plugin, connection, item, kind, into=into)
    if pair:
        pairing.pair(connection, item, record)
    return record
