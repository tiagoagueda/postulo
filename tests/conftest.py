import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from postulo.config import sqlite
from postulo.core import site


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix, request):
    """A run with browser tests in it keeps its database in a file (#1024).

    The suite's database is SQLite in memory, which is what makes it quick. An in-memory
    database is a single connection, so pytest-django's live server hands the test thread's
    connection to every thread it answers a request on; and every request is a transaction
    (`ATOMIC_REQUESTS`), so two requests in flight at once tangled their transactions in the
    one wrapper's bookkeeping. One committed under the other's savepoint, and the browser job
    died of `no such savepoint` and then of 1,829 `TransactionManagementError`s, a few hundred
    tests in, at random: run again alone, every one of them passed.
    `tests/e2e/test_live_server_connections.py` plays that order out.

    In a file every thread opens a connection of its own, and pytest-django passes none of
    them along: it shares only an in-memory one. The file is opened as the instance opens its
    own (`config/sqlite.py`, #206): a transaction takes the write lock as it begins, so a
    second request waits its turn for twenty seconds at most rather than failing at once, and
    under WAL the test thread reads while a request writes. One file per process, in a
    directory named after the xdist worker, removed when the run is over.

    The price is the instance's own: requests take turns, a page that only reads included,
    so a test that holds a request inside its view holds every other request behind it. The
    alternative, SQLite's deferred transactions, would let readers through, but a request
    that reads and then writes -- a session, a rate-limit count in the database cache, a
    field saved as it is typed -- fails at once with *database is locked* when another wrote
    in between, since SQLite does not wait out an upgrade (#206): the random failure this is
    here to end, in another form, and one an instance never meets. So a test that wants a
    slow answer holds it after the view, as `held` in `e2e/test_site_search.py` does.

    Only when the run has a browser test in it. The rest of the suite has no live server, and
    stays in memory; a PostgreSQL run (`POSTULO_TEST_DATABASE_URL`) is left as it is.
    """
    from django.conf import settings

    database = settings.DATABASES["default"]
    browsing = any(item.get_closest_marker("e2e") for item in request.session.items)
    if not browsing or not sqlite.is_sqlite(database):
        yield
        return
    worker = getattr(request.config, "workerinput", {}).get("workerid", "main")
    directory = Path(tempfile.mkdtemp(prefix=f"postulo-e2e-{worker}-"))
    database.setdefault("TEST", {})["NAME"] = str(directory / "postulo.sqlite3")
    options = database.setdefault("OPTIONS", {})
    for key, value in sqlite.FILE_OPTIONS.items():
        options.setdefault(key, value)
    yield
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def user(db):
    """A saved, ordinary user."""
    return get_user_model().objects.create_user(
        email="applicant@example.org", password="not-a-real-password"
    )


@pytest.fixture
def other_user(db):
    """A second user, for proving that data never leaks between accounts."""
    return get_user_model().objects.create_user(
        email="someone.else@example.org", password="not-a-real-password"
    )


@pytest.fixture(autouse=True)
def _no_inherited_language():
    """Every test starts in the instance's own language, whatever the last one left active.

    `LocaleMiddleware` activates a language per request and nothing deactivates it
    afterwards, so a test that signs in as somebody reading Postulo in Portuguese leaves
    Portuguese active in the thread for every test that follows. It shows up as a test
    asserting an English string and being handed a translated one -- passing alone, failing
    in company, and failing differently depending on which tests ran before it, which is the
    worst shape a failure can have.

    Reset after rather than before, so a test that activates a language on purpose is left
    alone while it runs and cleans up after itself either way.
    """
    from django.utils import translation

    yield
    translation.deactivate()


@pytest.fixture(autouse=True)
def _no_inherited_site_settings():
    """No test is handed the instance's policy row, or a plugin decision, as the last test
    left it (#231).

    `site.current()` memoises the row for the request that is reading it, and a test is not
    a request: the memo is dropped at a request boundary, by `SiteSettings.save`, and at the
    top of a scheduler pass, none of which happens between two tests in the same worker. A
    test that saved a row therefore handed it to the next one, whose database had been rolled
    back underneath it -- passing alone, failing in company, which is the worst shape a
    failure can have.

    Cleared either side, so a test that reads the row before touching it starts from the
    database rather than from whatever ran before it.
    """
    from postulo.core import site
    from postulo.plugins import policy

    site.forget_current()
    policy.forget_decisions()
    yield
    site.forget_current()
    policy.forget_decisions()


@pytest.fixture(autouse=True)
def _no_inherited_environment(monkeypatch):
    """No test inherits the developer's `.env`.

    `site.overridden_by` asks `os.environ` whether a variable is set, and settings are read
    from a `.env` file into `os.environ` at import. So a machine with a `.env` was running a
    different suite from one without: `POSTULO_DEFAULT_FROM_EMAIL=postulo@localhost` in a
    local file pinned the email from-address, and the test that saves one from the interface
    passed in CI and failed on the machine that wrote it.

    Cleared for every test; a test that wants a pinned variable sets it itself, which also
    makes the pinning visible in the test rather than in somebody's untracked file.
    """
    for variable in site.env_variables():
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(autouse=True)
def _no_inherited_drafts():
    """No test is handed a PDF another test drew (#220).

    `documents.pdf` keeps the last few *draft* renders in the process, keyed by the SHA-256
    of their HTML, so that reloading a report download does not redraw it. A worker is one
    process and so is the suite: without this, a test that stands in for the renderer and a
    test that expects a real one would answer each other across a whole file, depending on
    the order they ran in.
    """
    from postulo.documents import pdf

    pdf.forget_drafts()
    yield
    pdf.forget_drafts()


#: How many languages a worker carries from one test file into the next before it lets go of
#: what it holds for each of them; see `release_languages`. The source language, the
#: instance's, and the one or two a file switches to.
LANGUAGES_KEPT = 4


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """After the last test of a file, a worker lets go of what it holds per language (#1033).

    After the file's own fixtures are torn down, so a module fixture that pointed Django at
    catalogues of its own has put the real ones back first.
    """
    try:
        return (yield)
    finally:
        if nextitem is None or nextitem.path != item.path:
            release_languages()


def release_languages(kept: int = LANGUAGES_KEPT) -> None:
    """Drop the per-language caches of a process that has met more than ``kept`` languages.

    Three caches hold something for every language a process has ever used, for as long as
    it lives. On an instance that is right: a process serves the few languages its people
    read, and each is paid for once. A test worker is the one process that meets all 86 of
    them -- the suite renders every page in every language, reads month names in each and
    looks up every language's name in every other -- and then carries them through the
    thousands of tests left. Measured one process through the suite in order (2026-10-29),
    they were most of what made a worker grow towards 1 GB, and CI could not give the unit
    job a third worker inside its 2 GB container (#1033):

    - **The compiled catalogues**: Django's merged translation per language
      (`trans_real._translations`) and, below it, Python's own cache of every `.mo` file
      it ever opened (`gettext._translations`), one per catalogue set per language -- 2,717
      of them, about 110 MB. Python's cache is keyed by path, so the catalogues a fixture
      compiled into a temporary directory stayed in it after the directory was gone.
    - **The URL tables**: Django builds `reverse()`'s lookup tables once per active
      language, in every resolver of the tree and in each namespace resolver it caches,
      although Postulo translates no route: about 1.3 MB a language, 350,000 objects for all
      of them.
    - **CLDR's locale data**: Babel keeps every locale it loads, with its parents, and
      `language_names` keeps the `Locale` objects; naming languages in every language loads
      all of them, about 75 MB.

    All three fill again on demand, so a file that uses a language after this pays a few
    milliseconds to load it again. Most files use one or two and never reach ``kept``, so
    for them nothing is dropped. The memory freed is not handed back to the system, but the
    next file reuses it rather than growing the worker further.
    """
    import gettext

    from babel import localedata
    from django.urls import URLResolver, clear_url_caches, get_resolver
    from django.utils.translation import trans_real

    from postulo.core import language_names

    opened = {Path(path).parent.parent.name for _class, path in gettext._translations}
    if len(trans_real._translations) > kept or len(opened) > kept:
        trans_real._translations = {}
        trans_real._default = None
        gettext._translations.clear()

    root = get_resolver()
    if len(root._reverse_dict) > kept:

        def forget(resolver):
            resolver._reverse_dict.clear()
            resolver._namespace_dict.clear()
            resolver._app_dict.clear()
            for pattern in resolver.url_patterns:
                if isinstance(pattern, URLResolver):
                    forget(pattern)

        # The resolvers `include()` made live in the URL modules, not in a cache, so their
        # tables are emptied in place; the cached ones, namespace resolvers included, go.
        forget(root)
        clear_url_caches()

    # A locale loads its parents too (`root`, `en`, `en_001`...), hence more than one each.
    if len(localedata._cache) > 3 * kept:
        language_names._locale.cache_clear()
        localedata._cache.clear()


#: A handful of ESCO skills in three languages, for the tests that need the skills file and
#: must not need the real one (#266). Identifiers and names as v1.2.1 has them, bar the last
#: three: a skill with no French name, and two that share a Portuguese one.
ESCO_SKILLS = {
    "21c5790c-0930-4d74-b3b0-84caf5af12ea": {
        "en": "manage budgets",
        "fr": "gérer les budgets",
        "pt": "gerir orçamentos",
    },
    "598de5b0-5b58-4ea7-8058-a4bc4d18c742": {"en": "SQL", "fr": "SQL", "pt": "SQL"},
    "7111b95d-0ce3-441a-9d92-4c75d05c4388": {
        "en": "project management",
        "fr": "gestion de projets",
        "pt": "gestão de projetos",
    },
    "ccd0a1d9-afda-43d9-b901-96344886e14d": {
        "en": "Python (computer programming)",
        "fr": "Python (programmation informatique)",
        "pt": "Python (programação informática)",
    },
    "e0000000-0000-4000-8000-000000000001": {
        "en": "teamwork principles",
        "fr": "",
        "pt": "princípios do trabalho em equipa",
    },
    "e0000000-0000-4000-8000-000000000002": {
        "en": "use spreadsheets software",
        "fr": "utiliser un tableur",
        "pt": "usar folhas de cálculo",
    },
    "e0000000-0000-4000-8000-000000000003": {
        "en": "spreadsheet software",
        "fr": "logiciel de tableur",
        "pt": "usar folhas de cálculo",
    },
}


def write_esco_skills(directory, skills=None, revision: str = "9.9.9"):
    """Write a skills file into ``directory`` the way `fetch_esco` writes one."""
    from postulo.jobs.management.commands import fetch_esco

    skills = ESCO_SKILLS if skills is None else skills
    identifiers = sorted(skills)
    languages = sorted({language for names in skills.values() for language in names})
    names = {
        language: [skills[identifier].get(language, "") for identifier in identifiers]
        for language in languages
    }
    about = {
        "revision": f"ESCO v{revision}",
        "source": "https://esco.ec.europa.eu",
        "publisher": fetch_esco.PUBLISHER,
        "licence": "EUPL 1.2",
        "languages": languages,
        "skills": len(identifiers),
    }
    target = directory / f"esco-skills-{revision}.zip"
    fetch_esco.write_skills(target, about, identifiers, names)
    return target


@pytest.fixture
def esco_skills(tmp_path, monkeypatch):
    """The ESCO skills, as the small file above, where the loader looks for them.

    Nothing else is in that directory, so the occupations are absent for the test, which is
    the state they are in wherever they have not been downloaded. What the process held of
    the classification is dropped either side.
    """
    from postulo.jobs import esco

    directory = tmp_path / "esco"
    directory.mkdir()
    write_esco_skills(directory)
    monkeypatch.setattr(esco, "DATA_DIR", directory)
    esco.forget()
    yield directory
    esco.forget()


@pytest.fixture
def no_esco(tmp_path, monkeypatch):
    """No ESCO files at all, as on an instance where nobody ran `fetch_esco`."""
    from postulo.jobs import esco

    directory = tmp_path / "no-esco"
    directory.mkdir()
    monkeypatch.setattr(esco, "DATA_DIR", directory)
    esco.forget()
    yield directory
    esco.forget()


class InstalledSource:
    """A capture source installed on the instance rather than shipped inside it.

    Since #200 that difference decides whose switch a plugin is: what Postulo ships is the
    administrator's, what was installed is the person's. The registry's third-party loader
    is what says a plugin was installed, so this stands in for one.
    """

    name = "example-source"
    version = "1.0"
    kind = "source"

    def can_handle(self, url: str) -> bool:
        return False

    def parse(self, url: str, html: str):
        return None


@pytest.fixture
def third_party(monkeypatch):
    """The name of a source installed on the instance, registered for the test."""
    from postulo.plugins import registry

    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [InstalledSource()] if kind == "source" else [],
    )
    registry.plugins("source", refresh=True)
    try:
        yield InstalledSource.name
    finally:
        registry._cache.pop("source", None)


@pytest.fixture
def german(monkeypatch):
    """German active, with the catalogue entries a test hands it, whatever is compiled (#392).

    ``german({"msgid": "Nachricht"})`` writes those entries into the German catalogue for the
    test and returns the context in which they are read. A test of *how a sentence is built*
    needs a translation that differs from the English, and the compiled catalogues are not
    this suite's to depend on.
    """
    from contextlib import contextmanager

    from django.utils import translation
    from django.utils.translation import trans_real

    @contextmanager
    def active(entries: dict[str, str]):
        with translation.override("de"):
            catalogue = trans_real.translation("de")._catalog._catalogs[0]
            for msgid, text in entries.items():
                monkeypatch.setitem(catalogue, msgid, text)
            yield

    return active


@pytest.fixture
def openstreetmap(settings):
    """The map's tiles from OpenStreetMap, the default an instance gets (#898).

    The test settings draw the world's outline instead, so that no browser walk through the
    companies' map asks a server outside the project for tiles (#1033). The tests about the
    tiles themselves ask for OpenStreetMap here, and the browser's ones answer its requests
    without leaving the machine.
    """
    from postulo.jobs import tiles

    settings.POSTULO_MAP_TILES = tiles.OSM


@pytest.fixture
def atomic_requests(monkeypatch):
    """Requests run in a transaction each, as an instance runs them (#357).

    The test settings replace the database and leave `ATOMIC_REQUESTS` off, so a view which
    forgot `non_atomic_requests` looks fine in the suite. Use with `django_db(transaction=True)`
    and assert `not connection.in_atomic_block` at the point the remote call is made.
    """
    from django.db import connections

    monkeypatch.setitem(connections.settings["default"], "ATOMIC_REQUESTS", True)
