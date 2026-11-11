"""One registry of environment variables, and everything that reads or describes them (#1186).

Postulo read 120-odd `POSTULO_*` variables through `env(...)` calls scattered over the
settings and a few `os.environ` reads elsewhere, each with its own default and its own idea of
what a value may say. `.env.example` and the wiki's *Configuration* page were written by hand
and had drifted from the code: 31 names missing from the one, 17 from the other, two
documented that nothing read. Nothing said which spellings a boolean took, and a typo in a
name was a setting silently not made.

Now every variable is declared once, in `postulo.core.configuration`, and these tests hold
the rest to it: every name read anywhere is declared and every declared name is read; the
declared defaults are what the settings end up with; the syntaxes added (suffixed sizes and
durations, every boolean spelling, `_FILE` secrets) are taken without changing what an old
value meant; the start-up check names what it cannot read; the names follow the rules in
CONTRIBUTING.md, but for the list of today's exceptions; and `.env.example` is what the
registry writes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from io import StringIO
from pathlib import Path

import pytest
from django.core.checks import WARNING
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command

from postulo.core import checks, configuration, site
from postulo.core.configuration import Alias, Kind, Scope, Variable, read

REPO = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------------------- the names


#: Names that look like variables and are not: settings computed from one, and keys of a
#: request's META. Each stays out of the registry for the reason beside it.
NOT_VARIABLES = {
    # Whether POSTULO_OIDC_LINK_BY_EMAIL was set at all: a setting, computed.
    "POSTULO_OIDC_LINK_BY_EMAIL_CHOSEN",
    # The provider's id in allauth: a constant setting.
    "POSTULO_OIDC_PROVIDER_ID",
    # Where the proxy's own address is kept on a request: request.META, not the environment.
    "POSTULO_PROXY_ADDR",
    "POSTULO_UNTRUSTED_FORWARDER",
}

#: Declared names read where no pattern below can see it, and by what.
READ_ELSEWHERE = {
    # Arrives with the branch that adds the cached test database (#1144), which reads it in
    # `postulo.plugins.testdb` through a constant; the merge meets it here.
    "POSTULO_TEST_DB_CACHE": "postulo.plugins.testdb",
    # gunicorn reads it; the image sets it.
    "GUNICORN_CMD_ARGS": "gunicorn",
}

#: Where variables are read or set: the code, the image, the maintainers' scripts and CI.
SCANNED = ("src/postulo", "docker", "scripts", ".forgejo/workflows")
#: Where a declared name may be the one reading it: the above, and the test suite.
READERS = (*SCANNED, "tests")

#: The files a variable is read or set in: code, scripts, the image and CI.
_SOURCES = (".py", ".sh", ".yml", ".yaml", ".toml")
_NAME = re.compile(r"\b(POSTULO_[A-Z0-9_]*[A-Z0-9])\b")
#: The ways a name is read: through the registry, `os.environ`, a shell or a Dockerfile.
_READ_PATTERNS = (
    r'\bread\(\s*"{name}"',
    r'\bis_set\(\s*"{name}"',
    r'environ(?:\.get)?\s*[\[(]\s*"{name}"',
    r'"{name}"\s+in\s+os\.environ',
    r"\$\{{?{name}\b",
    r"^ARG\s+{name}\b",
    r'\("{name}",\s*"POSTGRES_PASSWORD"\)',
)


def _files(*roots: str):
    for root in roots:
        for path in (REPO / root).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix not in _SOURCES and path.name != "Dockerfile":
                continue
            if path.name in ("test_configuration.py", "configuration.py", ".env.example"):
                continue
            try:
                yield path, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


def test_every_name_the_code_image_scripts_and_ci_use_is_declared():
    """A `POSTULO_*` name written anywhere is one the registry knows, so nothing reads or sets
    a variable the start-up check would call unknown."""
    known = configuration.known_names()
    stray = {}
    for path, text in _files(*SCANNED):
        for name in _NAME.findall(text):
            if name not in known and name not in NOT_VARIABLES:
                stray.setdefault(name, path.relative_to(REPO).as_posix())
    assert not stray, f"declare these in postulo.core.configuration: {stray}"


def test_every_declared_name_is_read_somewhere():
    """A declaration nothing reads is documentation of a setting that does nothing."""
    texts = [text for _path, text in _files(*READERS)]
    unread = []
    for name in configuration.VARIABLES:
        if name in READ_ELSEWHERE:
            continue
        patterns = [re.compile(p.format(name=re.escape(name)), re.M) for p in _READ_PATTERNS]
        if not any(pattern.search(text) for text in texts for pattern in patterns):
            unread.append(name)
    assert not unread, f"declared and read by nothing: {unread}"


def test_the_fields_the_environment_pins_are_the_ones_the_registry_says():
    """The Server pages lock a field by `site.ENV_OVERRIDES`; the registry says the same."""
    declared = {}
    for variable in configuration.VARIABLES.values():
        if variable.field:
            declared.setdefault(variable.field, []).append(variable.name)
    overrides = {
        field: list(names) if isinstance(names, tuple) else [names]
        for field, names in site.ENV_OVERRIDES.items()
    }
    assert declared == overrides


def test_the_words_a_choice_takes_are_the_ones_the_code_acts_on():
    from postulo.core import mail_auth
    from postulo.core.mail_choices import MailGrant
    from postulo.jobs import geocoding

    variables = configuration.VARIABLES
    assert set(variables["POSTULO_EMAIL_OAUTH_PROVIDER"].choices) == set(mail_auth.PROVIDERS)
    assert set(variables["POSTULO_EMAIL_OAUTH_GRANT"].choices) == set(MailGrant.values)
    assert set(variables["POSTULO_GEOCODER"].choices) == {geocoding.OFF, *geocoding.SERVICES}
    assert checks.CHOICES["POSTULO_PDF_BACKEND"] == ("auto", "weasyprint", "chromium")
    assert len(checks.CHOICES) == 6


def test_every_variable_is_in_an_area_and_documented_in_one_place():
    for variable in configuration.VARIABLES.values():
        assert variable.area in configuration.AREA_BY_KEY, variable.name
        assert variable.description and "\n" not in variable.description, variable.name
        assert not variable.name.endswith(configuration.FILE_SUFFIX), variable.name
        if variable.kind in (Kind.SIZE, Kind.DURATION):
            assert variable.unit, variable.name


# ---------------------------------------------------------------- the naming rules

#: Every name that broke the rules in CONTRIBUTING.md when they were written, with the name it
#: takes when it is renamed (stage 2 of #1186; the old one stays as an alias for a minor).
#: Entries leave this list as they are renamed, and none is ever added: a new name follows
#: the rules.
BREAKERS = {
    # One word: there is no area.
    "POSTULO_DEBUG": "POSTULO_DEBUG_ENABLED",
    # A first word that is not an area.
    "POSTULO_ALLOW_WEAK_SECRET_KEY": "POSTULO_SECRET_KEY_WEAK_ALLOWED",
    "POSTULO_ALLOW_WILDCARD_HOSTS": "POSTULO_ALLOWED_HOSTS_WILDCARD_ALLOWED",
    "POSTULO_DEFAULT_FROM_EMAIL": "POSTULO_EMAIL_FROM",
    "POSTULO_SCRUB_EVERY_DAYS": "POSTULO_MEDIA_SCRUB_EVERY_SECONDS",
    "POSTULO_APT_REFRESH": "POSTULO_IMAGE_APT_REFRESH",
    "POSTULO_EXTRA_PACKAGES": "POSTULO_IMAGE_EXTRA_PACKAGES",
    "POSTULO_EXTRA_APT_PACKAGES": "POSTULO_IMAGE_EXTRA_APT_PACKAGES",
    "POSTULO_WRITE_GOLDEN": "POSTULO_TEST_WRITE_GOLDEN",
    # A unit other than bytes or seconds, or none on a duration.
    "POSTULO_BACKUP_UPLOAD_MAX_MB": "POSTULO_BACKUP_UPLOAD_MAX_BYTES",
    "POSTULO_UPLOAD_CEILING_MB": "POSTULO_UPLOAD_CEILING_BYTES",
    "POSTULO_EMAIL_TIMEOUT": "POSTULO_EMAIL_TIMEOUT_SECONDS",
    "POSTULO_EMAIL_CODE_TIMEOUT": "POSTULO_EMAIL_CODE_TIMEOUT_SECONDS",
    "POSTULO_EXPORT_KEEP_HOURS": "POSTULO_EXPORT_KEEP_SECONDS",
    "POSTULO_MEDIA_SWEEP_GRACE_HOURS": "POSTULO_MEDIA_SWEEP_GRACE_SECONDS",
    "POSTULO_CAPTURE_PAGE_KEEP_DAYS": "POSTULO_CAPTURE_PAGE_KEEP_SECONDS",
    # A boolean that is a negation; the new one is true when the step runs.
    "POSTULO_SKIP_MIGRATE": "POSTULO_CONTAINER_MIGRATE",
    "POSTULO_SKIP_PLUGIN_SYNC": "POSTULO_CONTAINER_PLUGIN_SYNC",
    "POSTULO_SKIP_GEONAMES": "POSTULO_CONTAINER_GEONAMES_FETCH",
    # One word.
    "POSTULO_GEOCODER": "POSTULO_GEOCODER_SERVICE",
    "POSTULO_WIKI": "POSTULO_WIKI_DIR",
}

#: The list may only shrink. Lower this as names are renamed; never raise it.
MOST_BREAKERS = 21

_SHAPE = re.compile(r"^POSTULO_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_UNIT_WORDS = {
    "B",
    "KB",
    "MB",
    "GB",
    "BYTE",
    "MS",
    "MINUTES",
    "MINS",
    "HOURS",
    "DAYS",
    "WEEKS",
    "SECS",
}
_NEGATIONS = {"SKIP", "NO", "NOT", "DISABLE", "DISABLED"}
_UNITS = {Kind.SIZE: "BYTES", Kind.DURATION: "SECONDS", Kind.RATE: "RATE"}


def rule_breaks(variable: Variable) -> list[str]:
    """What is wrong with a name, by the rules in CONTRIBUTING.md; nothing for a good one."""
    name = variable.name
    if not name.startswith("POSTULO_"):
        return []  # another program's name: gunicorn's, Django's, PostgreSQL's
    if not _SHAPE.match(name):
        return ["not POSTULO_ + an area + a thing, in upper snake case"]
    words = name.removeprefix("POSTULO_").split("_")
    found = []
    if variable.kind is not Kind.RATE and words[0] not in configuration.AREA_WORDS:
        found.append(f"{words[0]} is not an area word")
    if _UNIT_WORDS & set(words):
        found.append("a unit other than _BYTES, _SECONDS or _RATE")
    wanted = _UNITS.get(variable.kind)
    if wanted and words[-1] != wanted:
        found.append(f"a {variable.kind} ends in _{wanted}")
    if words[-1] in set(_UNITS.values()) - ({wanted} if wanted else set()):
        found.append(f"_{words[-1]} on a {variable.kind}")
    if variable.kind is Kind.DURATION and words[-1] == "TIMEOUT":
        found.append("a duration without its unit")
    if variable.kind is Kind.BOOLEAN and _NEGATIONS & set(words):
        found.append("a boolean that is a negation")
    return found


def test_every_name_follows_the_rules_but_the_listed_ones():
    breaking = {
        variable.name: problems
        for variable in configuration.VARIABLES.values()
        if (problems := rule_breaks(variable))
    }
    new = {name: why for name, why in breaking.items() if name not in BREAKERS}
    assert not new, f"these names break the rules in CONTRIBUTING.md: {new}"
    mended = sorted(set(BREAKERS) - set(breaking))
    assert not mended, f"these follow the rules now; take them off BREAKERS: {mended}"


def test_the_list_of_exceptions_only_shrinks():
    assert len(BREAKERS) <= MOST_BREAKERS
    for old, new in BREAKERS.items():
        variable = configuration.VARIABLES[old]
        assert not rule_breaks(Variable(new, variable.kind, variable.area, "x")), new
        assert new not in configuration.VARIABLES, f"{new} is already declared"


@pytest.mark.parametrize(
    ("name", "kind", "breaks"),
    [
        ("POSTULO_CAPTURE_SOURCE_MAX_BYTES", Kind.SIZE, False),
        ("POSTULO_CAPTURE_SOURCE_MAX_MB", Kind.SIZE, True),
        ("POSTULO_EMAIL_TIMEOUT", Kind.DURATION, True),
        ("POSTULO_EMAIL_TIMEOUT_SECONDS", Kind.DURATION, False),
        ("POSTULO_EMAIL_TIMEOUT_SECONDS", Kind.INTEGER, True),
        ("POSTULO_REGISTRATION_OPEN", Kind.BOOLEAN, False),
        ("POSTULO_CAPTURE_SKIP_ROBOTS", Kind.BOOLEAN, True),
        ("POSTULO_WHATEVER_THING", Kind.TEXT, True),
        ("POSTULO_ANYTHING_RATE", Kind.RATE, False),
        ("POSTULO_email_host", Kind.TEXT, True),
    ],
)
def test_the_rules_catch_what_they_say(name, kind, breaks):
    assert bool(rule_breaks(Variable(name, kind, "server", "x"))) is breaks


# ------------------------------------------------------------------------- the defaults

_DEFAULTS_SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    from pathlib import Path
    import environ

    environ.Env.read_env = lambda *args, **kwargs: None
    import importlib
    from postulo.core import configuration

    module = importlib.import_module(os.environ["DJANGO_SETTINGS_MODULE"])
    which = os.environ["DJANGO_SETTINGS_MODULE"].rsplit(".", 1)[-1]

    def plain(value):
        if isinstance(value, (Path, environ.Path)):
            return str(value)
        if isinstance(value, tuple):
            return list(value)
        return value

    skipped = set(json.loads(sys.argv[1]))
    compared, wrong = [], {}
    for variable in configuration.VARIABLES.values():
        if variable.scope is not configuration.Scope.INSTANCE or variable.module != which:
            continue
        if variable.name in skipped:
            continue
        name = variable.setting or variable.name
        compared.append(variable.name)
        if not hasattr(module, name):
            wrong[variable.name] = "no setting " + name
        elif plain(getattr(module, name)) != plain(variable.default):
            wrong[variable.name] = [repr(getattr(module, name)), repr(variable.default)]
    print(json.dumps({"compared": compared, "wrong": wrong}))
    """
)

#: Variables whose value does not land in a setting as it is read, and where it goes instead.
NOT_PINNED = {
    "POSTULO_DATABASE_URL": "DATABASES, parsed",
    "POSTULO_DATABASE_PASSWORD": "DATABASES, beside the URL",
    "POSTGRES_PASSWORD": "DATABASES, beside the URL",
    "POSTULO_CACHE_URL": "CACHES, parsed",
    "POSTULO_RATELIMIT_DIR": "CACHES['ratelimit']",
    "POSTULO_LOG_LEVEL": "LOGGING",
    "POSTULO_FETCH_PORTS": "POSTULO_FETCH_PORTS, as numbers",
    "POSTULO_ALLOW_WEAK_SECRET_KEY": "a refusal at start-up",
    "POSTULO_ALLOW_WILDCARD_HOSTS": "a refusal at start-up",
    "POSTULO_GEOLOCATIONS_DIR": "postulo.jobs.places.DATA_DIR",
    "POSTULO_ESCO_DIR": "postulo.jobs.esco.DATA_DIR",
    "POSTULO_GEONAMES_POSTCODES": "manage.py fetch_geonames",
}


def _defaults(module: str, **extra: str) -> dict:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("POSTULO_")}
    environment.pop("POSTGRES_PASSWORD", None)
    environment.update(extra)
    environment["DJANGO_SETTINGS_MODULE"] = f"postulo.config.settings.{module}"
    done = subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", _DEFAULTS_SCRIPT, json.dumps(sorted(NOT_PINNED))],
        capture_output=True,
        text=True,
        env=environment,
        cwd=REPO,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_each_declared_default_is_what_the_settings_end_up_with():
    """With nothing in the environment, every setting a variable lands in holds the default the
    registry declares and documents, in the base settings and in the production ones."""
    base = _defaults("base")
    prod = _defaults("prod", POSTULO_SECRET_KEY="k" * 20 + "Zx9-kq2PLm7vRt4WsN8yHb3JcF6dGa1Ue5")

    assert base["wrong"] == {}
    assert prod["wrong"] == {}
    assert "POSTULO_SECRET_KEY" in base["compared"] and "POSTULO_HSTS_SECONDS" in prod["compared"]
    # Every variable the settings read is either compared or said above to land elsewhere.
    read_by_settings = {
        v.name
        for v in configuration.VARIABLES.values()
        if v.scope is Scope.INSTANCE and v.module in ("base", "prod")
    }
    assert read_by_settings == set(base["compared"]) | set(prod["compared"]) | set(NOT_PINNED)


# --------------------------------------------------------------------------- the values

FLAG = configuration.VARIABLES["POSTULO_MEDIA_SWEEP"]  # a boolean that defaults to true


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        *[(word, True) for word in ("true", "TRUE", "True", "yes", "Yes", "on", "ON", "1")],
        *[(word, False) for word in ("false", "FALSE", "no", "No", "off", "OFF", "0")],
        # What django-environ also took, still meaning what it meant.
        ("y", True),
        ("ok", True),
        ("2", True),
        ("n", False),
    ],
)
def test_a_boolean_takes_every_spelling(raw, value):
    environ = {"POSTULO_MEDIA_SWEEP": raw}
    assert read("POSTULO_MEDIA_SWEEP", environ=environ) is value
    assert configuration.problems(environ, {FLAG.name: FLAG}) == []


def test_a_boolean_that_is_not_one_reads_as_false_and_is_refused():
    """django-environ read it as false and said nothing; it still reads as false, and start-up
    now says so."""
    environ = {"POSTULO_MEDIA_SWEEP": "treu"}

    assert read("POSTULO_MEDIA_SWEEP", environ=environ) is False
    [problem] = configuration.problems(environ, {FLAG.name: FLAG})
    assert problem.level == "error"
    assert "true or false" in problem.hint


def test_an_empty_boolean_that_defaults_to_true_is_a_warning():
    environ = {"POSTULO_MEDIA_SWEEP": ""}

    assert read("POSTULO_MEDIA_SWEEP", environ=environ) is False
    [problem] = configuration.problems(environ, {FLAG.name: FLAG})
    assert problem.level == "warning"


@pytest.mark.parametrize(
    ("name", "raw", "value"),
    [
        # A bare number is in the unit the name gives, as before.
        ("POSTULO_UPLOAD_CEILING_MB", "25", 25),
        ("POSTULO_UPLOAD_CEILING_MB", "25MB", 25),
        ("POSTULO_UPLOAD_CEILING_MB", "1GB", 1024),
        ("POSTULO_UPLOAD_CEILING_MB", "1.5 gb", 1536),
        ("POSTULO_LOG_MAX_BYTES", "2MB", 2 * 1024 * 1024),
        ("POSTULO_LOG_MAX_BYTES", "512KiB", 512 * 1024),
        ("POSTULO_LOG_MAX_BYTES", "100B", 100),
        ("POSTULO_HSTS_SECONDS", "90s", 90),
        ("POSTULO_HSTS_SECONDS", "2h", 7200),
        ("POSTULO_HSTS_SECONDS", "15m", 900),
        ("POSTULO_EXPORT_KEEP_HOURS", "2h", 2),
        ("POSTULO_EXPORT_KEEP_HOURS", "2d", 48),
        ("POSTULO_EXPORT_KEEP_HOURS", "48", 48),
        ("POSTULO_SCRUB_EVERY_DAYS", "7d", 7),
        ("POSTULO_SCRUB_EVERY_DAYS", "1w", 7),
        ("POSTULO_CACHE_MAX_ENTRIES", " 5000 ", 5000),
    ],
)
def test_sizes_and_durations_take_a_suffix(name, raw, value):
    assert read(name, environ={name: raw}) == value
    assert configuration.problems({name: raw}, {name: configuration.VARIABLES[name]}) == []


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("POSTULO_UPLOAD_CEILING_MB", "100KB"),  # not a whole number of megabytes
        ("POSTULO_EXPORT_KEEP_HOURS", "90m"),
        ("POSTULO_EXPORT_KEEP_HOURS", "1.5h"),
        ("POSTULO_EMAIL_PORT", "abc"),
        ("POSTULO_EMAIL_PORT", ""),
        ("POSTULO_LOG_MAX_BYTES", "5 megabytes"),
    ],
)
def test_a_number_that_cannot_be_read_takes_the_default_and_is_an_error(name, raw):
    """It used to stop the instance at import, before any check could name it."""
    variable = configuration.VARIABLES[name]

    assert read(name, environ={name: raw}) == variable.default
    [problem] = configuration.problems({name: raw}, {name: variable})
    assert problem.level == "error"
    assert name in problem.message and name in problem.hint


def test_a_number_out_of_range_is_kept_and_warned_about():
    name = "POSTULO_EMAIL_PORT"

    assert read(name, environ={name: "70000"}) == 70000
    [problem] = configuration.problems({name: "70000"}, {name: configuration.VARIABLES[name]})
    assert problem.level == "warning"


def test_a_list_is_split_on_commas_without_the_spaces():
    environ = {"POSTULO_ALLOWED_HOSTS": " a.example.org , b.example.org,,"}

    assert read("POSTULO_ALLOWED_HOSTS", environ=environ) == ["a.example.org", "b.example.org"]
    assert read("POSTULO_ALLOWED_HOSTS", environ={}) == ["localhost", "127.0.0.1"]


def test_a_choice_takes_its_words_in_any_case_and_nothing_else():
    assert read("POSTULO_PDF_BACKEND", environ={"POSTULO_PDF_BACKEND": " Chromium "}) == "chromium"
    assert read("POSTULO_GEOCODER", environ={"POSTULO_GEOCODER": ""}) == ""

    geocoder = configuration.VARIABLES["POSTULO_GEOCODER"]
    [problem] = configuration.problems({"POSTULO_GEOCODER": "nominatum"}, {geocoder.name: geocoder})
    assert problem.level == "error" and "nominatim" in problem.hint


def test_a_variable_another_check_refuses_is_left_to_it():
    """A rate and the six words of `postulo.E001` were checked before; not twice."""
    environ = {"POSTULO_CAPTURE_RATE": "30/hour", "POSTULO_PDF_BACKEND": "weasy"}

    assert configuration.problems(environ) == []
    assert read("POSTULO_CAPTURE_RATE", environ=environ) == "30/hour"


def test_a_value_naming_another_variable_is_read_as_django_environ_read_it():
    """`$NAME` was another variable's value under django-environ; it still is."""
    environ = {"POSTULO_OIDC_NAME": "$COMPANY_SSO", "COMPANY_SSO": "Keycloak"}

    assert read("POSTULO_OIDC_NAME", environ=environ) == "Keycloak"


def test_an_unset_secret_with_no_default_is_none_and_an_empty_one_too():
    assert read("POSTULO_SECRET_KEY", environ={}) is None
    assert read("POSTULO_SECRET_KEY", environ={"POSTULO_SECRET_KEY": ""}) is None


# ------------------------------------------------------------------------------ secrets


def test_a_secret_can_be_given_as_a_file(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("a-long-random-token\n", encoding="utf-8")
    environ = {"POSTULO_LOGS_TOKEN_FILE": str(secret)}

    assert read("POSTULO_LOGS_TOKEN", environ=environ) == "a-long-random-token"
    assert configuration.is_set("POSTULO_LOGS_TOKEN", environ=environ)
    found = configuration.source("POSTULO_LOGS_TOKEN", environ=environ)
    assert (found.source, found.via) == ("file", "POSTULO_LOGS_TOKEN_FILE")


def test_only_one_trailing_newline_is_taken_off_a_secret_file(tmp_path):
    secret = tmp_path / "token"
    secret.write_bytes(b"  spaced token \r\n")

    environ = {"POSTULO_LOGS_TOKEN_FILE": str(secret)}
    assert read("POSTULO_LOGS_TOKEN", environ=environ) == "  spaced token "


def test_a_secret_given_both_ways_is_refused_naming_both(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("from-the-file", encoding="utf-8")
    environ = {"POSTULO_LOGS_TOKEN": "from-the-variable", "POSTULO_LOGS_TOKEN_FILE": str(secret)}

    with pytest.raises(ImproperlyConfigured) as refused:
        read("POSTULO_LOGS_TOKEN", environ=environ)

    message = str(refused.value)
    assert "POSTULO_LOGS_TOKEN and POSTULO_LOGS_TOKEN_FILE" in message
    assert "from-the" not in message


def test_a_secret_file_that_cannot_be_read_is_refused_naming_the_path(tmp_path):
    missing = tmp_path / "nowhere" / "token"
    environ = {"POSTULO_LOGS_TOKEN_FILE": str(missing)}

    with pytest.raises(ImproperlyConfigured) as refused:
        read("POSTULO_LOGS_TOKEN", environ=environ)

    assert str(missing) in str(refused.value)
    assert "POSTULO_LOGS_TOKEN_FILE" in str(refused.value)


def test_only_a_secret_has_a_file_form():
    assert "POSTULO_LOGS_TOKEN_FILE" in configuration.known_names()
    assert "POSTULO_LOG_DIR_FILE" not in configuration.known_names()
    assert read("POSTULO_LOG_LEVEL", environ={"POSTULO_LOG_LEVEL_FILE": "/x"}) == "INFO"


def test_a_password_given_as_a_file_pins_its_field(monkeypatch, tmp_path):
    """The Server pages lock a field the environment sets; a `_FILE` sets it too."""
    secret = tmp_path / "password"
    secret.write_text("pw", encoding="utf-8")
    monkeypatch.delenv("POSTULO_EMAIL_HOST_PASSWORD", raising=False)
    monkeypatch.setenv("POSTULO_EMAIL_HOST_PASSWORD_FILE", str(secret))

    assert site.overridden_by("email_password") == "POSTULO_EMAIL_HOST_PASSWORD"


# ----------------------------------------------------------------------------- aliases

SCRATCH = {
    "POSTULO_SCRATCH_LIMIT_BYTES": Variable(
        "POSTULO_SCRATCH_LIMIT_BYTES",
        Kind.SIZE,
        "storage",
        "A scratch entry, to prove a rename.",
        default=1024,
        unit="bytes",
        aliases=(Alias("POSTULO_SCRATCH_LIMIT_MB", unit="megabytes"),),
    )
}


def test_an_old_name_still_works_in_its_own_unit():
    environ = {"POSTULO_SCRATCH_LIMIT_MB": "3"}

    assert read("POSTULO_SCRATCH_LIMIT_BYTES", environ=environ, variables=SCRATCH) == 3 * 1024**2
    assert configuration.deprecated(environ, SCRATCH) == [
        ("POSTULO_SCRATCH_LIMIT_MB", "POSTULO_SCRATCH_LIMIT_BYTES")
    ]
    assert configuration.unknown(environ, SCRATCH) == []
    found = configuration.source("POSTULO_SCRATCH_LIMIT_BYTES", environ=environ, variables=SCRATCH)
    assert (found.source, found.via) == ("alias", "POSTULO_SCRATCH_LIMIT_MB")


def test_a_suffixed_value_under_an_old_name_means_what_it_says():
    environ = {"POSTULO_SCRATCH_LIMIT_MB": "2KB"}

    assert read("POSTULO_SCRATCH_LIMIT_BYTES", environ=environ, variables=SCRATCH) == 2048


def test_an_old_name_and_the_new_one_both_set_are_refused():
    environ = {"POSTULO_SCRATCH_LIMIT_MB": "3", "POSTULO_SCRATCH_LIMIT_BYTES": "5"}

    with pytest.raises(ImproperlyConfigured, match="POSTULO_SCRATCH_LIMIT_MB"):
        read("POSTULO_SCRATCH_LIMIT_BYTES", environ=environ, variables=SCRATCH)


def test_start_up_names_the_new_name_of_an_old_one(monkeypatch):
    monkeypatch.setattr(configuration, "VARIABLES", {**configuration.VARIABLES, **SCRATCH})
    monkeypatch.setenv("POSTULO_SCRATCH_LIMIT_MB", "3")

    [warning] = [m for m in checks.environment() if m.id == "postulo.W007"]

    assert "POSTULO_SCRATCH_LIMIT_MB" in warning.msg
    assert "POSTULO_SCRATCH_LIMIT_BYTES" in warning.hint


# ----------------------------------------------------------------------- the start-up check


def _ids(monkeypatch, **variables: str) -> dict[str, list]:
    for name in [name for name in os.environ if name.startswith("POSTULO_")]:
        monkeypatch.delenv(name)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    found: dict[str, list] = {}
    for message in checks.environment():
        found.setdefault(message.id, []).append(message)
    return found


def test_an_unknown_name_is_a_warning_naming_the_nearest_known_one(monkeypatch):
    found = _ids(monkeypatch, POSTULO_REGISTRATON_OPEN="true")

    [warning] = found["postulo.W005"]
    assert "POSTULO_REGISTRATON_OPEN" in warning.msg
    assert "POSTULO_REGISTRATION_OPEN" in warning.hint
    assert warning.level == WARNING


def test_the_test_and_ci_names_are_known(monkeypatch):
    found = _ids(
        monkeypatch,
        POSTULO_TEST_DATABASE_URL="sqlite:///x",
        POSTULO_TEST_DB_CACHE="0",
        POSTULO_SEED_PASSWORD="ci-only",
        POSTULO_SKIP_MIGRATE="1",
        POSTULO_SKIP_PLUGIN_SYNC="1",
    )

    assert found == {}


def test_a_value_that_cannot_be_read_is_an_error_with_the_syntax(monkeypatch):
    found = _ids(monkeypatch, POSTULO_EMAIL_PORT="smtp", POSTULO_SKIP_MIGRATE="true")

    errors = {message.msg.split(" ")[0]: message for message in found["postulo.E009"]}
    assert set(errors) == {"POSTULO_EMAIL_PORT", "POSTULO_SKIP_MIGRATE"}
    assert "whole number" in errors["POSTULO_EMAIL_PORT"].hint
    assert "1 to switch it on" in errors["POSTULO_SKIP_MIGRATE"].hint


def test_a_value_out_of_range_is_a_warning(monkeypatch):
    found = _ids(monkeypatch, POSTULO_LOG_BACKUPS="-1")

    assert [message.msg.split(" ")[0] for message in found["postulo.W006"]] == [
        "POSTULO_LOG_BACKUPS"
    ]


# -------------------------------------------------------------------- the generated files


def _script():
    path = REPO / "scripts" / "configuration_docs.py"
    spec = importlib.util.spec_from_file_location("configuration_docs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_example_is_what_the_registry_writes():
    assert _script().main(["--check"]) == 0, "run scripts/configuration_docs.py"


def test_env_example_carries_every_variable_an_instance_reads():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    script = _script()

    for variable in configuration.VARIABLES.values():
        if script.in_env_file(variable):
            assert re.search(rf"^(# )?{variable.name}=", text, re.M), variable.name
        else:
            assert not re.search(rf"^(# )?{variable.name}=", text, re.M), variable.name
    # Only what production needs is set; everything else is shown commented out.
    assert re.findall(r"^[A-Z][A-Z0-9_]+=", text, re.M) == [
        "POSTULO_SECRET_KEY=",
        "POSTULO_ALLOWED_HOSTS=",
    ]


def test_env_example_holds_no_path_of_the_machine_that_wrote_it():
    text = (REPO / ".env.example").read_text(encoding="utf-8")

    assert str(REPO) not in text and REPO.as_posix() not in text


def test_the_wiki_tables_have_every_documented_variable_once():
    markdown = _script().render_wiki()

    for variable in configuration.VARIABLES.values():
        rows = re.findall(rf"^\| `{variable.name}` \|", markdown, re.M)
        assert len(rows) == (1 if variable.documented else 0), variable.name
    for area in configuration.AREAS:
        documented = [v for v in configuration.in_area(area.key) if v.documented]
        assert (f"### {area.title}" in markdown) is bool(documented)


# ------------------------------------------------------------------- manage.py settings


def test_the_settings_command_says_where_each_value_came_from(monkeypatch, settings, tmp_path):
    secret = tmp_path / "token"
    secret.write_text("never-printed-token-value", encoding="utf-8")
    monkeypatch.delenv("POSTULO_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("POSTULO_METRICS_TOKEN_FILE", str(secret))
    monkeypatch.setenv("POSTULO_LOGS_TOKEN", "another-never-printed-value")
    # What the settings were built with, had the process started with that environment.
    settings.POSTULO_METRICS_TOKEN = "never-printed-token-value"
    settings.POSTULO_LOGS_TOKEN = "another-never-printed-value"
    monkeypatch.setenv("POSTULO_CACHE_URL", "redis://user:hunter2-secret@cache:6379/1")
    monkeypatch.setenv("POSTULO_TIME_ZONE", "UTC")

    out = StringIO()
    call_command("settings", "--json", stdout=out)
    entries = {entry["name"]: entry for entry in json.loads(out.getvalue())}
    text = StringIO()
    call_command("settings", stdout=text)

    assert entries["POSTULO_METRICS_TOKEN"]["source"] == "file"
    assert entries["POSTULO_METRICS_TOKEN"]["value"] == "set"
    assert entries["POSTULO_LOGS_TOKEN"]["value"] == "set"
    assert entries["POSTULO_FIELD_KEY"]["value"] == "unset"
    assert entries["POSTULO_TIME_ZONE"]["source"] == "environment"
    assert entries["POSTULO_UPDATE_CHECK"]["source"] == "default"
    assert "POSTULO_TEST_DATABASE_URL" not in entries
    for printed in (out.getvalue(), text.getvalue()):
        assert "never-printed" not in printed
        assert "hunter2" not in printed
    assert "redis://user:********@cache:6379/1" in text.getvalue()


def test_the_settings_command_shows_the_development_variables_when_asked():
    out = StringIO()
    call_command("settings", "--json", "--all", stdout=out)

    assert "POSTULO_TEST_DATABASE_URL" in {entry["name"] for entry in json.loads(out.getvalue())}
