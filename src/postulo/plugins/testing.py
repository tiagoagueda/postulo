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

**And a source of company details held to its contract (#1256).**
`assert_is_a_company_enrichment_source` checks a `company-enrichment` plugin against answers
you recorded: its sources are usable, every address it asks for is one it declared, what it
reads out of the answers is what it said it offers, each value saying where it was read, and
none of it reaches the network. It returns the values as Postulo would hold them.

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

**And a dashboard widget, checked and drawn (#1102).** `assert_declares_usable_widgets` holds
a plugin's ``widgets`` to what the dashboard accepts, and `draw_widget` draws one body as the
dashboard would -- handed the person and a connection, rendered, and held to the allowlist --
so a plugin's tests assert on what a person sees.

**And the environment variables a plugin reads, checked (#1186).**
`assert_declares_usable_environment` holds a plugin's ``environment`` to what Postulo accepts:
each an `EnvironmentVariable` of a known kind, named ``POSTULO_PLUGIN_<NAME>_...`` by the rules
Postulo's own names follow, none of them one of Postulo's, and its default readable as its kind.

**And a job service's search held to its contract (#1253).** `assert_is_a_listing_search`
takes one `ListingSearch` and an answer recorded from its service for a search and for one
advert, and fails unless Postulo could use it: a key, hosts and criteria of the right shape,
every address it describes -- for ordinary searches and for words written to break out of a
parameter -- on its own hosts over https and the same whatever was typed, the recorded answers
read into hits a capture can be made from, and a source and a licence stated.
**And an importer held to its contract (#1260).** `assert_is_an_importer` hands an importer
files of the plugin's own -- ones it reads, ones it must leave alone -- and holds it to what
the import page relies on: `can_handle` answers and never raises, `read` returns the `Record`
on the surface with the keys the writer reads and dates that are dates, a file it will not
read is an `ImportRefused` and nothing else, and nothing is written to a database.

**And the migrated test database, kept between runs (#1144).** A plugin's suite migrates the
whole of Postulo before its first test. `cached_django_db_setup` makes the fixture that does
it once and restores the result afterwards; two lines in the plugin's ``tests/conftest.py``:

    from postulo.plugins.testing import cached_django_db_setup

    django_db_setup = cached_django_db_setup()

**And a sync run as Postulo runs it, with what it kept read back (#1165).** `sync_state` and
`SyncReport.state` are on the surface and the column that holds the state is not. `run_sync`
runs a connection the way the scheduler and *Sync now* do -- so a state is kept or refused by
the same rules -- optionally from a state of the test's choosing, and `kept_sync_state` reads
what the next run will be handed.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
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


@contextmanager
def _network_refused():
    """Nothing connects and no name resolves while this is open: a source describes its
    requests and reads the answers, and Postulo is the one that dials."""
    import socket

    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "a company-enrichment source reached for the network: Postulo makes every "
            "request, and hands the source the answers"
        )

    resolve, connect = socket.getaddrinfo, socket.socket.connect
    socket.getaddrinfo, socket.socket.connect = refuse, refuse
    try:
        yield
    finally:
        socket.getaddrinfo, socket.socket.connect = resolve, connect


def assert_is_a_company_enrichment_source(plugin, *, subject, answers, key: str = "") -> list:
    """Hold a company-enrichment plugin to its contract, against answers you recorded (#1256).

    For a source's own test suite: ``subject`` is an `EnrichmentSubject` for a company of
    your choosing and ``answers`` the `EnrichmentAnswer`s your service gave for it, recorded
    once and kept with the tests. ``key`` names the source where the plugin has several.

    Fails unless the plugin answers to the protocol and its kind; every source can be used
    (its key, what it starts from and offers, exactly one of ``hosts`` and ``own_site``);
    every address ``addresses(subject)`` and ``follow(answers, subject)`` return is one
    Postulo would ask -- ``https`` on a declared host, or on the host of the subject's
    website for a source that reads the company's own site, and four at most in all; and
    ``read(answers, subject)`` returns only `OfferedValue`s for fields the source offers,
    each with an origin. All of it runs with the network refused: a source that dials
    fails here.

    Returns the offered values cleaned as Postulo would hold them, for your own assertions
    -- a value the cleaning drops (an address that is not ``http``, a country nobody knows)
    is not in it.
    """
    from postulo.jobs import enrichment

    from .base import CompanyEnrichmentPlugin

    assert isinstance(plugin, CompanyEnrichmentPlugin), (
        f"{plugin!r} does not provide name, kind and sources"
    )
    assert plugin.kind == "company-enrichment", f"its kind is {plugin.kind!r}"
    sources = list(plugin.sources)
    assert sources, "the plugin contributes no source"
    for source in sources:
        wrong = enrichment.problems(source)
        assert not wrong, f"{getattr(source, 'key', source)!r}: " + "; ".join(wrong)
    keys = [source.key for source in sources]
    assert len(keys) == len(set(keys)), f"two sources share a key: {keys}"
    if key:
        assert key in keys, f"the plugin has no source {key!r}"
        source = sources[keys.index(key)]
    else:
        assert len(sources) == 1, f"say which source with key=: {keys}"
        source = sources[0]

    allowed = set(source.hosts) or {enrichment.host_of(subject.website)}
    schemes = ("https",) if source.hosts else ("http", "https")

    def held(addresses) -> list[str]:
        found = [str(address) for address in addresses or ()]
        for address in found:
            assert address.split(":", 1)[0] in schemes and enrichment.host_of(address) in allowed, (
                f"{source.key!r} asks for {address}, which is not {' or '.join(schemes)} on "
                f"{sorted(allowed)}"
            )
        return found

    answers = tuple(answers)
    with _network_refused():
        asked = held(source.addresses(subject))
        assert asked, f"{source.key!r} names no address for this subject"
        if source.follow is not None:
            asked += held(source.follow(answers, subject))
        assert len(asked) <= enrichment.MAX_REQUESTS, (
            f"{source.key!r} asks for {len(asked)} addresses; one press allows "
            f"{enrichment.MAX_REQUESTS}"
        )
        offered = list(source.read(answers, subject) or ())
    for value in offered:
        assert isinstance(value, enrichment.Offered), f"read returned {value!r}"
        assert value.field in source.offers, (
            f"{source.key!r} returned a value for {value.field!r}, which it does not offer"
        )
        assert str(value.origin or "").strip(), (
            f"the {value.field!r} value {value.value!r} does not say where it was read"
        )
    return enrichment.cleaned_all(offered, source)


#: What a person might type, and what somebody might type to make a search ask elsewhere: each
#: has to reach a parameter's value and nothing else.
_HOSTILE_WORDS = (
    "nurse",
    "sjuksköterska Göteborg",
    "a&q=b&offset=9999",
    "../../admin?x=1#top",
    "https://elsewhere.example/",
    "x\r\nHost: elsewhere.example",
    "\u202ereversed\u0000",
    "%2F%2E%2E%2F",
    "y" * 500,
)


def assert_is_a_listing_search(search, *, results, advert) -> list:
    """Hold one job service's `ListingSearch` to what Postulo needs of it (#1253).

    ``results`` is the JSON a search answered with, recorded from the service (cut down, with
    nobody's name, address or number left in it), and ``advert`` is ``(key, document)``: one
    advert's number and the JSON the service answered for it. Nothing is asked of the network:
    the search describes requests, and reads what it is handed.

    Fails unless the search is one the registry would keep; unless every address it describes,
    for a spread of criteria and for words written to break out of a parameter, is ``https``
    on one of its hosts and carries no query of its own, and is the same address whatever was
    typed; unless the recorded search reads into at least one hit, each a number a service's
    own could be and a posting with a title and a page, as Postulo would show it; unless the
    recorded advert reads into one posting; and unless a source and a licence are stated.
    Returns the hits, cleaned as the search page shows them, for the caller's own assertions.
    """
    from urllib.parse import urlsplit

    from postulo.jobs import listing_search as core

    assert isinstance(search, core.ListingSearch), f"{search!r} is not a ListingSearch"
    assert core._usable(search), (
        f"{search.key!r} would be left out: check its key (lower case, digits, hyphens, at most "
        "thirty), its countries (two capitals each), its languages (BCP 47 tags), its hosts "
        f"(names), and that it supports some of {core.FILTERS} and nothing else"
    )
    assert str(search.source).strip(), "a search names its source: it is a listing's found via"
    assert search.licence.strip(), "a search states the licence its service's adverts are under"

    paths = set()
    for words in _HOSTILE_WORDS:
        for criteria in (
            core.Criteria(words=words),
            core.Criteria(words=words, place=words, remote=True, since_days=7, page=3),
        ):
            honoured = core.honoured(search, criteria)
            if honoured.is_empty():
                continue
            try:
                address = core._address(search, search.request(honoured))
            except core.SearchRefused as refused:
                raise AssertionError(
                    f"for {honoured!r} the search described an address Postulo refuses: {refused}"
                ) from refused
            parts = urlsplit(address)
            assert parts.scheme == "https" and parts.hostname in search.hosts, address
            paths.add((parts.hostname, parts.path))
    assert len(paths) == 1, f"what was typed changed the address asked: {sorted(paths)}"

    criteria = core.honoured(search, core.Criteria(words="nurse"))
    found = search.read(results, criteria)
    assert isinstance(found, core.Results), f"read returned {found!r}, not SearchResults"
    hits = []
    for hit in found.hits:
        assert isinstance(hit, core.Hit), f"{hit!r} is not a SearchHit"
        assert core._is_advert(hit.key), f"{hit.key!r} is not a number a service's own can be"
        posting = core.cleaned(search, hit.posting)
        assert posting.title and posting.url, f"{hit.key!r} has no title or no page"
        hits.append(core.Hit(key=hit.key, posting=posting))
    assert hits, "the recorded search was read into no hits"

    key, document = advert
    try:
        address = core._address(search, search.one(key))
    except core.SearchRefused as refused:
        raise AssertionError(f"for {key!r} the search described an address Postulo refuses") from (
            refused
        )
    parts = urlsplit(address)
    assert parts.scheme == "https" and parts.hostname in search.hosts, address
    one = search.read_one(document, key)
    assert one is not None, f"the recorded advert {key!r} was read as no such advert"
    posting = core.cleaned(search, one)
    assert posting.title and posting.url, f"the advert {key!r} has no title or no page"
    return hits


#: What an importer's ``accepts`` may hold: a file extension, or a media type.
_ACCEPTED = re.compile(r"\.[A-Za-z0-9]{1,12}|[a-z]+/[A-Za-z0-9][A-Za-z0-9.+-]*")

#: The keys of a record's ``person``, and of the ``address`` inside it.
_PERSON_KEYS = frozenset(
    {
        "first_name",
        "last_name",
        "headline",
        "location",
        "website",
        "phone",
        "email",
        "orcid",
        "address",
    }
)
_ADDRESS_KEYS = frozenset({"street", "postcode", "municipality", "region", "country"})

#: The values of a row that are not text: everything else that is not a date is.
_NOT_TEXT = {
    "skills": (list,),
    "levels": (dict,),
    "eqf_level": (int, type(None)),
    "hours": (int, type(None)),
}

#: What changes a database, as the first word of a statement.
_WRITES = frozenset(
    {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "TRUNCATE"}
)

#: Bytes no importer reads: what `can_handle` and `read` must survive without a traceback.
_GARBAGE = (
    bytes(range(256)) * 4,
    b"\x00" * 64,
    b"{",
    b"[",
    b"<",
    b'{"',
    b"PK\x03\x04",
    b"%PDF-1.7",
    b"\xff\xfe\x00\x00",
    b"a,b,c\n1,2,3\n",
)


def _record_problems(record) -> list[str]:
    """What is wrong with a record an importer returned, in words; nothing where it is sound."""
    import datetime as dt
    import json

    from postulo.resume.importing import ROWS, Record

    if not isinstance(record, Record):
        return [f"read returned {type(record).__name__}, not the Record on the plugin surface"]
    found: list[str] = []
    for name in ("source", "source_label", "locale"):
        if not isinstance(getattr(record, name), str):
            found.append(f"{name} is {type(getattr(record, name)).__name__}, not text")
    for name in ("skipped", "notes"):
        said = getattr(record, name)
        if not isinstance(said, list) or not all(isinstance(each, str) for each in said):
            found.append(f"{name} is not a list of sentences")
    person = record.person
    if not isinstance(person, dict):
        found.append("person is not a dictionary")
    else:
        unknown = sorted(set(person) - _PERSON_KEYS)
        if unknown:
            found.append(f"person has keys nothing reads: {unknown}")
        address = person.get("address", {})
        if not isinstance(address, dict):
            found.append("person's address is not a dictionary")
        else:
            unknown = sorted(set(address) - _ADDRESS_KEYS)
            if unknown:
                found.append(f"the address has keys nothing reads: {unknown}")
        for key, value in [
            *person.items(),
            *(address.items() if isinstance(address, dict) else ()),
        ]:
            if key != "address" and not isinstance(value, str):
                found.append(f"person's {key} is {type(value).__name__}, not text")
    for name, rows in ROWS.items():
        entries = getattr(record, name)
        if not isinstance(entries, list):
            found.append(f"{name} is not a list")
            continue
        for place, row in enumerate(entries):
            where = f"{name}[{place}]"
            if not isinstance(row, dict):
                found.append(f"{where} is not a dictionary")
                continue
            missing = [key for key in rows.required if key not in row]
            if missing:
                found.append(f"{where} has no {missing}")
            unknown = sorted(set(row) - set(rows.known))
            if unknown:
                found.append(f"{where} has keys nothing reads: {unknown}")
            for key, value in row.items():
                if key in rows.dates:
                    a_day = isinstance(value, dt.date) and not isinstance(value, dt.datetime)
                    if value is not None and not a_day:
                        found.append(f"{where}: {key} is {value!r}, not a datetime.date or None")
                elif key in rows.known and not isinstance(value, _NOT_TEXT.get(key, (str,))):
                    found.append(f"{where}: {key} is {type(value).__name__}")
                elif key in ("skills", "levels"):
                    try:
                        json.dumps(value)
                    except (TypeError, ValueError):
                        found.append(f"{where}: {key} holds something a session cannot keep")
            if name == "skill_groups" and isinstance(row.get("skills"), list):
                if not all(isinstance(skill, str) for skill in row["skills"]):
                    found.append(f"{where}: skills is not a list of names")
    return found


def assert_is_an_importer(plugin, *, samples: Iterable[bytes], not_samples: Iterable[bytes] = ()):
    """Hold an importer to its contract, with files of your own (#1260).

    For an importer's own test suite. ``samples`` are files it reads, as bytes;
    ``not_samples`` are files it must leave to somebody else (another importer's format,
    a word processor's file). It fails unless:

    - the plugin answers to the protocol, calls itself an ``"importer"`` and has a name, a
      label and a description; and its ``accepts``, if it declares one, is a tuple of file
      extensions (``".json"``) and media types (``"application/json"``);
    - ``can_handle`` says yes to each sample and no to each non-sample, and answers with a
      plain ``True`` or ``False``, raising nothing, for an empty file, for bytes that are
      no format at all and for each sample cut short;
    - ``read`` turns each sample into the `Record` on the surface, whose rows hold the keys
      the wiki's table lists and no others, whose dates are ``datetime.date`` or ``None``,
      and whose other values are text (``skills`` a list of names, ``levels`` a
      dictionary, ``hours`` and ``eqf_level`` a whole number or ``None``);
    - ``read`` given bytes that are no format, or a sample cut short, either returns a
      record or raises `ImportRefused`, and nothing else;
    - nothing is written to a database along the way: an importer turns bytes into a
      record and stops.

    What Postulo refuses before any importer is asked (`refuse_unreadable`) is not handed
    to ``read`` here either. The check needs no database of its own: an importer that asks
    one for anything fails where a test has not been given one.
    """
    from contextlib import ExitStack

    from django.db import connections

    from .base import ImporterPlugin, ImportRefused, refuse_unreadable

    assert isinstance(plugin, ImporterPlugin), (
        f"{plugin!r} does not provide name, version, kind, label, description, can_handle and read"
    )
    assert plugin.kind == "importer", f"its kind is {plugin.kind!r}"
    for name in ("name", "label", "description"):
        assert str(getattr(plugin, name) or ""), f"it has no {name}"
    accepts = getattr(plugin, "accepts", None)
    if accepts is not None:
        assert isinstance(accepts, tuple) and accepts, "accepts is a tuple that is not empty"
        odd = [
            each for each in accepts if not isinstance(each, str) or not _ACCEPTED.fullmatch(each)
        ]
        assert not odd, f"accepts holds neither an extension nor a media type: {odd}"

    good = [bytes(sample) for sample in samples]
    others = [bytes(sample) for sample in not_samples]
    assert good, "give at least one file the importer reads"
    cut = sorted(
        {sample[:length] for sample in good for length in (1, len(sample) // 4, len(sample) // 2)}
        | {sample[:-1] for sample in good}
    )

    statements: list[str] = []

    def watch(execute, sql, params, many, context):
        statements.append(str(sql))
        return execute(sql, params, many, context)

    def asked(data: bytes) -> bool:
        try:
            answer = plugin.can_handle(data, "")
        except Exception as error:
            raise AssertionError(
                f"can_handle raised {type(error).__name__} on {data[:24]!r}: it answers what "
                "a file is, and a file it does not know is a no"
            ) from error
        assert answer is True or answer is False, f"can_handle returned {answer!r}, not a bool"
        return answer

    def unreadable(data: bytes) -> bool:
        try:
            refuse_unreadable(data)
        except ImportRefused:
            return True
        return False

    with ExitStack() as stack:
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(watch))

        for data in (b"", *_GARBAGE, *cut):
            asked(data)
        for data in others:
            assert not asked(data), f"can_handle claims a file that is not its own: {data[:40]!r}"
        for data in good:
            assert asked(data), f"can_handle does not recognise the sample {data[:40]!r}"
            record = plugin.read(data)
            problems = _record_problems(record)
            assert not problems, "; ".join(problems[:20])
        for data in (*_GARBAGE, *cut):
            if unreadable(data):
                continue
            try:
                record = plugin.read(data)
            except ImportRefused:
                continue
            except Exception as error:
                raise AssertionError(
                    f"read raised {type(error).__name__} on {data[:24]!r}: a file it will "
                    "not read is an ImportRefused, with a sentence for the person"
                ) from error
            problems = _record_problems(record)
            assert not problems, "; ".join(problems[:20])

    written = [sql for sql in statements if sql.lstrip().split(None, 1)[0].upper() in _WRITES]
    assert not written, f"the importer wrote to the database: {written[:3]}"


def assert_declares_usable_widgets(plugin) -> None:
    """Fail unless every dashboard widget the plugin declares can be put on a dashboard (#1102).

    What Postulo checks when it reads the declaration, with the reasons it would only have
    logged: each is a `DashboardWidget` with a key, a label, a sentence, a template, a
    function and one of the three widths, no two have one key, and each template is found.
    A plugin with no widgets passes.
    """
    from django.template.loader import get_template

    from .base import widget_problems, widgets_of
    from .themes import register_plugin_themes

    register_plugin_themes(plugin)
    declared = widgets_of(plugin)
    problems = [problem for widget in declared for problem in widget_problems(widget)]
    assert not problems, "; ".join(problems)
    keys = [widget.key for widget in declared]
    assert len(keys) == len(set(keys)), f"two widgets share a key: {keys}"
    for widget in declared:
        get_template(widget.template)


def assert_declares_usable_environment(plugin) -> None:
    """Fail unless every environment variable the plugin declares is one Postulo can read (#1186).

    What the start-up check would only log: each is an `EnvironmentVariable` of one of the
    kinds, its name begins ``POSTULO_PLUGIN_`` and the plugin's name and follows the naming
    rules, it is not one of Postulo's own, no two share a name, and reading it with nothing
    set gives its default. A plugin that declares nothing passes.
    """
    from .base import environment_of, environment_problems, read_environment

    name = str(getattr(plugin, "name", "") or "")
    assert name, "the plugin has no name"
    declared = environment_of(plugin)
    problems = [found for variable in declared for found in environment_problems(variable, name)]
    assert not problems, "; ".join(problems)
    names = [variable.name for variable in declared]
    assert len(names) == len(set(names)), f"two variables share a name: {names}"
    for variable in declared:
        assert read_environment(variable, environ={}) == variable.default, (
            f"{variable.name}: reading it unset does not give its default"
        )


def draw_widget(plugin, key: str, user, connection=None) -> str:
    """One of the plugin's dashboard widgets as the dashboard draws its body (#1102).

    Handed what the dashboard hands it -- the person, and one of their connections to the
    plugin or ``None`` -- and returned as the markup that reaches the page: rendered, then
    held to the dashboard's allowlist, so a test sees what a person would and not what the
    template wrote. What the plugin raises is raised here, where the dashboard would leave
    the widget out and log it.
    """
    from postulo.core.widgets import draw

    from .base import widgets_of
    from .themes import register_plugin_themes

    register_plugin_themes(plugin)
    found = [widget for widget in widgets_of(plugin) if widget.key == key]
    assert found, f"{plugin!r} declares no widget {key!r}"
    return draw(found[0], user, connection)


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


def cached_django_db_setup(*, also: Iterable[Path | str] = ()):
    """pytest-django's ``django_db_setup``, with the migrated database kept between runs (#1144).

    Returns a session fixture named ``django_db_setup``; assign it in ``tests/conftest.py``::

        from postulo.plugins.testing import cached_django_db_setup

        django_db_setup = cached_django_db_setup()

    The first run migrates as before and keeps a copy of the migrated database; a later run
    restores the copy into its in-memory SQLite database instead of migrating. pytest-django's
    own fixture still does everything else -- which databases, serialised contents, teardown --
    because this one only stands around it. A database on another engine, one kept in a
    file, and a run with ``--no-migrations`` are migrated as they always were.

    **The copy is named by what the migrations are**: every migration file of every installed
    app (Postulo's, your plugin's, each library's), the modules they import, the settings
    they read by name, and the versions of everything installed. Change any of them and the
    next run migrates afresh. What the name cannot see is a file or a setting a ``RunPython``
    reaches through other code; if a migration of yours seeds rows from a data file, name it
    in ``also`` so that a change to the file is a change to the name::

        django_db_setup = cached_django_db_setup(also=[HERE / "data" / "regions.json"])

    **``POSTULO_TEST_DB_CACHE`` decides whether and where.** Unset, the copy is kept on a
    person's machine (under the user's cache directory, ``postulo/test-databases``) and not
    on CI, where ``CI`` is set and a job starts from nothing. ``0`` switches it off, ``1``
    switches it on, and any other value is the directory to keep it in -- which is what a
    workflow sets, together with a cache step for that directory, to migrate only when its
    Postulo or its own migrations change. ``--create-db`` migrates afresh and replaces the
    copy; deleting the directory clears it, and it is never needed for a run to pass.
    """
    import pytest

    from . import testdb

    files = tuple(also)

    @pytest.fixture(scope="session", name="django_db_setup")
    def django_db_setup(request, django_db_use_migrations, django_db_createdb):
        directory = testdb.cache_directory() if django_db_use_migrations else None
        with testdb.answering_migrate(directory, also=files, afresh=django_db_createdb):
            # The fixture this one replaces, by its own name: pytest hands back the next
            # one down, pytest-django's, and sets it up here, inside the block.
            request.getfixturevalue("django_db_setup")

    return django_db_setup


def migrated_database_header() -> str | None:
    """A line for ``pytest_report_header``: where the migrated database is kept, if it is.

    Optional, and worth its three lines in a ``conftest.py``: a run that restored its
    database says so at the top, with the variable that makes it migrate instead::

        def pytest_report_header(config):
            return migrated_database_header()
    """
    from . import testdb

    return testdb.report_header()


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


def run_sync(connection, *, state: dict | None = None):
    """Run a sync connection once, as the scheduler and *Sync now* do (#1165).

    Through the same door: the connection's turn is taken, the plugin runs as the owner,
    the report is recorded, and the state the run returned is kept, or refused, by Postulo's
    rules, not the test's. Returns the `SyncReport`; ``connection`` is the object the plugin
    was handed, and holds afterwards what was recorded.

    With ``state``, the run starts from it, as if the last run that did not fail had returned
    it: the way to put a plugin in front of a place it would otherwise take several runs to
    reach, or one a server has since made stale.
    """
    from .syncing import sync_connection

    if state is not None:
        connection.sync_state = dict(state)
        connection.save(update_fields=["sync_state", "updated_at"])
    return sync_connection(connection)


def kept_sync_state(connection) -> dict:
    """What the connection's next run will be handed, read back from the database (#1165).

    The same value `sync_state` gives a plugin at the start of that run, and a copy: empty
    where nothing is kept, where the last state was refused before any was, or after the
    connection's settings changed.
    """
    from .base import sync_state

    connection.refresh_from_db(fields=["sync_state"])
    return sync_state(connection)
