"""The migrated test database, kept between runs (#1144).

A test run starts by migrating an empty database, and Postulo has a few hundred migrations:
about a minute on a contributor's machine before the first test of one file runs, and the
same again in every plugin repository, whose suite migrates the whole of Postulo to test a
handful of functions. The test database is SQLite in memory, so there was nothing for
pytest-django's ``--reuse-db`` to keep.

This keeps it. The first run migrates as it always did and then copies the database into a
file, in a directory of the person's own; a later run copies that file back into its empty
in-memory database instead of migrating. Both copies are SQLite's own backup
(`sqlite3.Connection.backup`), which is how Django hands a test database to the workers of
its own parallel runner.

**The file is named by everything `migrate` reads**, as far as that can be known without
running it; `fingerprint` says what that is and what it cannot see. Change a migration and
the name changes, so the next run migrates afresh and the old file is left to be pruned.

**Nothing here is trusted over a migration.** A file is used only when it is whole, only for
an in-memory SQLite database with nothing in it, and only while migrations are on. Anything
unexpected -- a directory that cannot be written, a file that is not a database, a key that
cannot be worked out -- means the run migrates, as it would have without this.

`postulo.plugins.testing` publishes the fixture; this module is the machinery under it and
is not part of the promise.
"""

from __future__ import annotations

import ast
import errno
import hashlib
import os
import re
import secrets
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path

#: The one variable. Unset, the cache is on, except on CI; see `cache_directory`.
VARIABLE = "POSTULO_TEST_DB_CACHE"

#: Bumped when what a file holds, or what its name covers, changes: every older file is
#: then a different name, and is pruned in its turn.
FORMAT = 1

#: How many databases the directory keeps: the newest by last use. A few branches' worth,
#: so that going back to `main` between two pieces of work does not migrate again.
KEPT = 6

#: How long a process waits for another that is migrating the same database before it
#: migrates for itself. Longer than a migration takes on a machine with everything else
#: running on it; a process that died holding the lock has let go of it already.
LOCK_WAIT = 900.0

#: A file left under a temporary name for this long was abandoned by a run that was killed.
ABANDONED_AFTER = 3600.0

#: Settings that decide what `migrate` builds whether or not a migration names them. The
#: ones a migration does name are read from its source and added; see `settings_named`.
SETTINGS = (
    "INSTALLED_APPS",
    "MIGRATION_MODULES",
    "DATABASE_ROUTERS",
    "AUTH_USER_MODEL",
    "DEFAULT_AUTO_FIELD",
    "DEFAULT_TABLESPACE",
    "DEFAULT_INDEX_TABLESPACE",
    "CACHES",
    "LANGUAGE_CODE",
    "TIME_ZONE",
    "USE_TZ",
    "SITE_ID",
)

_OFF = {"0", "false", "no", "off"}
_ON = {"1", "true", "yes", "on"}

_SETTING_NAMED = re.compile(
    r"""\bsettings\.([A-Z][A-Z0-9_]*)|getattr\(\s*settings\s*,\s*["']([A-Z][A-Z0-9_]*)["']"""
)
#: An import of something other than Django: worth parsing the file to resolve.
_FOREIGN_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+(?!django\b)", re.MULTILINE)
#: `<function f at 0x7f...>`: the same setting in the next process, written differently.
_ADDRESS = re.compile(r" at 0x[0-9A-Fa-f]+")


# ------------------------------------------------------------------- whether, and where
def default_directory(
    environ: Mapping[str, str] | None = None, platform: str | None = None
) -> Path:
    """Where the databases are kept when nobody says: the person's own cache directory.

    ``%LOCALAPPDATA%`` on Windows, ``~/Library/Caches`` on macOS, and ``$XDG_CACHE_HOME`` or
    ``~/.cache`` anywhere else, each with ``postulo/test-databases`` under it. Never inside
    a checkout: a worktree made for one branch would start empty, and the files are the same
    for every checkout whose migrations are.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        root = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif platform == "darwin":
        root = str(Path.home() / "Library" / "Caches")
    else:
        root = environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "postulo" / "test-databases"


def cache_directory(environ: Mapping[str, str] | None = None) -> Path | None:
    """The directory the migrated database is kept in, or ``None`` where it is not kept.

    ``POSTULO_TEST_DB_CACHE`` decides: ``0`` (or ``false``, ``no``, ``off``) is never,
    ``1`` (``true``, ``yes``, ``on``) is always, in the default place, and anything else is
    always, in the directory it names. Unset or empty, it is kept everywhere but on CI,
    which is wherever ``CI`` is set to something: the Forgejo runner sets ``CI=true`` in
    every job, as GitHub's does, and it is what `tests/test_readme.py` already asks.

    CI is left out because a runner starts from nothing, so there would be no file to find
    and one more to write; a workflow that keeps the directory between runs asks for it.
    """
    environ = os.environ if environ is None else environ
    asked = environ.get(VARIABLE, "").strip()
    if asked.lower() in _OFF:
        return None
    if asked.lower() in _ON:
        return default_directory(environ)
    if asked:
        return Path(asked).expanduser()
    if environ.get("CI", "").strip():
        return None
    return default_directory(environ)


def report_header(environ: Mapping[str, str] | None = None) -> str | None:
    """A line for pytest's header: where a migrated database is kept, and how to stop it.

    The directory and not what this run did with it: the header is written before any
    database is set up, and a browser run or one on PostgreSQL migrates whatever is there.
    """
    directory = cache_directory(environ)
    if directory is None:
        return None
    return f"migrated test database: kept in {directory} ({VARIABLE}=0 migrates every run)"


# ------------------------------------------------------------------------------ the name
def digest(
    files: Iterable[tuple[str, Path]],
    facts: Iterable[str] = (),
    read: Callable[[Path], bytes] = Path.read_bytes,
) -> str:
    """One name for a set of files and facts: what the files say, not when they were saved.

    ``files`` are a label each and a path. The label goes into the name and the path does
    not, so two checkouts of the same migrations share a database, and a file that moved to
    another label is a different one. A file that cannot be read counts as missing, which is
    a different name from any content it could have had.
    """
    whole = hashlib.sha256()
    for fact in facts:
        whole.update(b"fact\0" + fact.encode("utf-8", "backslashreplace") + b"\0")
    for label, path in sorted(files):
        try:
            content = hashlib.sha256(read(Path(path))).hexdigest()
        except OSError:
            content = "missing"
        whole.update(b"file\0" + label.encode("utf-8", "backslashreplace") + b"\0")
        whole.update(content.encode("ascii") + b"\0")
    return whole.hexdigest()[:40]


def settings_named(source: str) -> set[str]:
    """The settings a migration reads by name: ``settings.X`` and ``getattr(settings, "X")``."""
    return {first or second for first, second in _SETTING_NAMED.findall(source)}


def modules_imported(source: str, package: str) -> set[str]:
    """What a migration imports that is neither Django nor Python's own, at any depth.

    ``from postulo.core import slugs`` is ``postulo.core.slugs``, as in
    `testing.imports_in`: the name is tried as a module first, since that is the file whose
    code the migration runs. Relative imports are resolved against ``package``, the
    migrations package the file is in.
    """
    if not _FOREIGN_IMPORT.search(source):
        return set()
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            names = [f"{module}.{alias.name}" for alias in node.names if alias.name != "*"]
            names.append(module)
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            top = name.partition(".")[0]
            if top and top not in {"django", "__future__"} | sys.stdlib_module_names:
                found.add(name)
    return found


def _file_of(name: str) -> Path | None:
    """The file a dotted name is the module of, or ``None`` where it is not a module."""
    from importlib.util import find_spec

    try:
        spec = find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None or not spec.origin or not spec.has_location:
        return None
    return Path(spec.origin)


def migration_files(app_configs=None) -> tuple[list[tuple[str, Path]], list[str]]:
    """Every migration file of every installed app, and the apps that have none.

    Found the way Django finds them, through ``MIGRATION_MODULES`` and the package's own
    search path, and read from disk without being imported: importing a few hundred
    migrations is part of what a restored run does not pay for. Every ``.py`` file of the
    package counts, the ones `migrate` would skip included; a file beside the migrations is
    there to be imported by one.
    """
    from importlib.util import find_spec

    from django.apps import apps
    from django.db.migrations.loader import MigrationLoader

    files: list[tuple[str, Path]] = []
    without: list[str] = []
    for config in apps.get_app_configs() if app_configs is None else app_configs:
        module, _explicit = MigrationLoader.migrations_module(config.label)
        try:
            spec = find_spec(module) if module else None
        except (ImportError, AttributeError, ValueError):
            spec = None
        locations = list(spec.submodule_search_locations or ()) if spec else []
        # No package, or a directory with no `__init__.py`, which Django takes for none: the
        # app's tables are made from its models. Whatever files are there count all the same.
        if not locations or spec.origin is None:
            without.append(config.label)
        for location in locations:
            for path in sorted(Path(location).glob("*.py")):
                files.append((f"{module}/{path.name}", path))
    return files, without


def _tables_without_migrations(connection, labels: Iterable[str]) -> list[str]:
    """The statements `migrate --run-syncdb` would run for the apps that have no migrations.

    Their tables come from the models as they are today, so the models are what the name
    has to cover: as the SQL they turn into, which is exactly what would be run.
    """
    from django.apps import apps

    models = [
        model
        for label in labels
        for model in apps.get_app_config(label).get_models()
        if model._meta.can_migrate(connection)
    ]
    if not models:
        return []
    with connection.schema_editor(collect_sql=True) as editor:
        for model in models:
            editor.create_model(model)
    return [str(statement) for statement in editor.collected_sql]


def fingerprint(connection, also: Iterable[Path | str] = ()) -> str:
    """The name of the database `migrate` would build on ``connection`` today.

    It covers:

    - every migration file of every installed app, by content: Postulo's, the plugins it
      ships, Django's, every library's, and in a plugin's own suite the plugin's;
    - the file of every module a migration imports, other than Django's and Python's: a
      field of Postulo's own decides a column's type, and a data migration runs the code it
      imports. One step only: not what those modules import in their turn;
    - the settings in `SETTINGS`, the database's engine, options and test options, and every
      setting a migration reads by name (`settings_named`);
    - for an app with no migrations, the tables its models turn into;
    - the versions of Python, Django, SQLite and every installed distribution, and the
      platform;
    - ``also``: files a migration reads that none of this can know about, named by whoever
      wrote the migration.

    **What it cannot see** is what a `RunPython` reaches without naming it in the migration:
    a module imported by a module it imports, a file one of those reads, a setting read by a
    function it calls, an environment variable, the network, the clock. Nor the code of a
    ``pre_migrate`` or ``post_migrate`` receiver, which is not a migration: what one wrote
    to the database is in the copy, and it is not called again on a run that restores.
    Postulo has none of its own; Django's make the content types and the permissions, from
    the models the migrations describe. Where rows are seeded from something in this
    paragraph, name the file in ``also``, or clear the directory.
    """
    import platform

    import django
    from django.conf import settings

    files, without = migration_files()
    named: set[str] = set(SETTINGS)
    imported: set[str] = set()
    # Each migration is read once, for what it names and then for the name itself.
    held: dict[Path, bytes] = {}
    for label, path in files:
        try:
            held[path] = path.read_bytes()
            source = held[path].decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        named |= settings_named(source)
        try:
            imported |= modules_imported(source, label.partition("/")[0])
        except SyntaxError:
            continue

    def read(path: Path) -> bytes:
        return held[path] if path in held else path.read_bytes()

    migrations = {path for _label, path in files}
    for name in sorted(imported):
        path = _file_of(name)
        if path is not None and path not in migrations:
            files.append((f"import/{name}", path))
    files.extend((f"also/{Path(path).name}", Path(path)) for path in also)

    database = connection.settings_dict
    facts = [
        f"format {FORMAT}",
        f"python {platform.python_version()} {sys.platform}",
        f"django {django.get_version()}",
        f"sqlite {sqlite3.sqlite_version}",
        f"alias {connection.alias}",
        f"engine {database.get('ENGINE')}",
        f"options {_plain(database.get('OPTIONS'))}",
        f"test {_plain(database.get('TEST'))}",
        *(f"setting {name} {_plain(getattr(settings, name, None))}" for name in sorted(named)),
        *(f"table {statement}" for statement in _tables_without_migrations(connection, without)),
        *(f"distribution {name}" for name in distributions()),
    ]
    return digest(files, facts, read)


def distributions(paths: Iterable[str] | None = None) -> list[str]:
    """Every installed distribution with its version, as its metadata directory is named.

    ``Django-6.1.3.dist-info`` says both, and listing a directory costs a fraction of what
    opening and parsing some two hundred metadata files does on every run.
    """
    found: set[str] = set()
    for root in sys.path if paths is None else paths:
        with suppress(OSError):
            found.update(
                entry.name
                for entry in os.scandir(root or ".")
                if entry.name.endswith((".dist-info", ".egg-info"))
            )
    return sorted(found)


def _plain(value) -> str:
    """A setting as text that reads the same in the next process."""
    return _ADDRESS.sub("", repr(value))


# ----------------------------------------------------------------------- the file itself
def is_whole(entry: Path) -> bool:
    """Whether a file is a SQLite database of the length its own first page says it has.

    A database is written under another name and renamed when it is complete (`store`), so
    a run that was killed leaves nothing under this one. This is for the rest: a disk that
    filled, a copy that was cut short, a file something else put there.
    """
    try:
        size = entry.stat().st_size
        with entry.open("rb") as handle:
            header = handle.read(100)
    except OSError:
        return False
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        return False
    page = int.from_bytes(header[16:18], "big")
    page = 65536 if page == 1 else page
    pages = int.from_bytes(header[28:32], "big")
    return pages > 0 and page * pages == size


def is_empty(database: sqlite3.Connection) -> bool:
    """Whether a database holds nothing at all: no table, index, view or trigger."""
    return database.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def restore(entry: Path, database: sqlite3.Connection) -> bool:
    """Copy the kept database at ``entry`` into ``database``; whether it was done.

    Only into a database with nothing in it, and only from a file that is whole. A copy
    that fails part of the way is rolled back by SQLite, so ``database`` is as it was.
    """
    if not is_whole(entry) or not is_empty(database):
        return False
    try:
        source = sqlite3.connect(f"{entry.resolve().as_uri()}?mode=ro", uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return False
    try:
        source.backup(database)
    except sqlite3.Error:
        return False
    finally:
        source.close()
    with suppress(OSError):
        os.utime(entry)  # the newest by last use is what `prune` keeps
    return True


def store(database: sqlite3.Connection, entry: Path) -> bool:
    """Copy ``database`` to ``entry``, which is there complete or not at all.

    Written beside it under a name of this process's own and renamed over it: a rename is
    one step, so no other run ever opens half a file, and of two runs that finish together
    the second's replaces the first's, which was the same database. A failure costs the
    next run a migration and this one nothing.
    """
    temporary = entry.with_name(f"{entry.stem}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    try:
        target = sqlite3.connect(temporary)
        try:
            database.backup(target)
        finally:
            target.close()
        os.replace(temporary, entry)
    except (sqlite3.Error, OSError):
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return False
    return True


def prune(directory: Path, kept: int = KEPT) -> None:
    """Keep the ``kept`` databases used last, and no file a killed run left behind."""
    now = time.time()
    dated = []
    for path in directory.iterdir():
        with suppress(OSError):
            age = now - path.stat().st_mtime
            if path.suffix == ".sqlite3":
                dated.append((age, path))
            elif path.suffix == ".tmp" and age > ABANDONED_AFTER:
                path.unlink()
    for _age, path in sorted(dated)[kept:]:
        with suppress(OSError):
            path.unlink()
            path.with_suffix(".lock").unlink(missing_ok=True)


@contextmanager
def locked(path: Path, wait: float = LOCK_WAIT) -> Iterator[bool]:
    """Hold ``path`` so that one process migrates while the others wait for its file.

    The operating system's own lock on an open file -- `msvcrt.locking` on Windows, `flock`
    elsewhere -- which it lets go of when the process ends, however it ends: a lock *file*
    that a killed run left behind would hold every later run up until somebody deleted it.

    Yields whether the lock is held. After ``wait`` seconds, or where the file cannot be
    opened or locked at all, it is not, and the caller goes on without it: nothing depends
    on the lock but a migration saved, since `store` is safe from two processes at once.
    """
    try:
        handle = path.open("a+b")
    except OSError:
        yield False
        return
    try:
        held = _take(handle, wait)
        try:
            yield held
        finally:
            if held:
                with suppress(OSError):
                    _let_go(handle)
    finally:
        handle.close()


#: What a lock that somebody else holds is refused with: `flock` says it would block, and
#: `msvcrt.locking` says access is denied (or, on some runtimes, that it would deadlock).
_HELD_BY_ANOTHER = {
    errno.EACCES,
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    errno.EDEADLK,
    getattr(errno, "EDEADLOCK", errno.EDEADLK),
}


def _take(handle, wait: float) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as error:
            # Held by another process is worth waiting for; a file system that has no
            # locks to give is not, and would be asked in vain until the wait ran out.
            if error.errno not in _HELD_BY_ANOTHER or time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


def _let_go(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# --------------------------------------------------------------------- migrate, or do not
def migrate_or_restore(
    database: Callable[[], sqlite3.Connection],
    migrate: Callable[[], object],
    entry: Path,
    *,
    afresh: bool = False,
    wait: float = LOCK_WAIT,
) -> str:
    """Fill an empty database from ``entry``, or migrate it and leave ``entry`` for the next run.

    ``database`` hands back the SQLite connection, asked for again after the migration has
    run on it. Returns ``"restored"`` or ``"migrated"``.

    Several processes that start together with nothing kept -- the workers of one
    ``pytest -n 4`` -- take turns: the first migrates and stores, the others wait for it and
    restore what it stored.

    ``afresh`` migrates whatever is kept and replaces it, and waits for nobody: every worker
    of a run asked to start afresh migrates, as each did before there was anything to keep,
    and taking turns at that would only make it four migrations end to end.
    """
    if not afresh and restore(entry, database()):
        return "restored"
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        migrate()
        return "migrated"
    with locked(entry.with_suffix(".lock"), 0 if afresh else wait):
        if not afresh and restore(entry, database()):
            return "restored"
        migrate()
        if store(database(), entry):
            with suppress(OSError):
                prune(entry.parent)
    return "migrated"


def _may_be_kept(connection) -> bool:
    """Whether this is a database the cache is for: SQLite, in memory, built by migrations.

    Another engine is migrated as before, and so is a test database kept in a file (the
    browser suite's), which has its own journal and its own connections.
    """
    if connection.vendor != "sqlite" or not connection.is_in_memory_db():
        return False
    return connection.settings_dict.get("TEST", {}).get("MIGRATE", True) is not False


@contextmanager
def answering_migrate(
    directory: Path | None,
    *,
    also: Iterable[Path | str] = (),
    afresh: bool = False,
    outcomes: list | None = None,
) -> Iterator[None]:
    """While this is open, the ``migrate`` that builds a test database may be a restore.

    Django's `create_test_db` creates the database, calls ``migrate`` and carries on, with
    no place between the two to say where the tables come from. So the one name it looks up
    to make that call, `django.core.management.call_command`, is answered here for as long
    as the databases are being set up and put back after: a ``migrate`` with nothing but
    options goes through `migrate_or_restore`, and every other command, and every
    ``migrate`` on a database that is not kept, runs as it was called.

    With no ``directory`` nothing is replaced at all, which is what CI gets.
    ``outcomes``, where given, collects ``(alias, "restored" | "migrated")``.
    """
    if directory is None:
        yield
        return

    from django.core import management
    from django.db import connections

    real = management.call_command

    def call_command(name, *args, **options):
        def migrate():
            return real(name, *args, **options)

        if name != "migrate" or args:
            return migrate()
        connection = connections[options.get("database") or "default"]
        if not _may_be_kept(connection):
            return migrate()
        try:
            connection.ensure_connection()
            entry = Path(directory) / f"{fingerprint(connection, also)}.sqlite3"
        except Exception:
            # Whatever went wrong in working the name out, a migration is always right.
            return migrate()
        outcome = migrate_or_restore(lambda: connection.connection, migrate, entry, afresh=afresh)
        if outcomes is not None:
            outcomes.append((connection.alias, outcome))
        return None

    management.call_command = call_command
    try:
        yield
    finally:
        management.call_command = real
