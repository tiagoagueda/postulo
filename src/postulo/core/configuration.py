"""Every environment variable Postulo reads, declared once (#1186).

A variable is declared here with its type, its default, the syntax its value takes and a
line saying what it does, and read through `read`. The settings modules call `read` rather
than `env(...)`, so a name, a default or a syntax is written in one place, and what reads it,
the start-up check, `manage.py settings`, `.env.example` and the wiki's *Configuration*
tables all come from the same declaration. `scripts/configuration_docs.py` writes the last
two, and `tests/test_configuration.py` holds the code, the registry and the files together.

This module is imported by the settings, before Django has settings to give: it imports no
model, no translation and nothing of Postulo's above the foundation.

**What a value may say.** Every syntax a variable accepted before still means what it meant;
what is new is only accepted in addition:

- a boolean is `true` or `false`, `yes` or `no`, `on` or `off`, `1` or `0`, in any case
  (`y`, `n`, `ok` and any whole number, which django-environ took, still work);
- a size is a number in the unit its name gives, or a number with `B`, `KB`, `MB`, `GB` or
  `TB` after it, in 1024s (`25MB`);
- a duration is a number in the unit its name gives, or a whole number with `s`, `m`, `h`,
  `d` or `w` after it (`90s`, `2h`, `7d`);
- a list is separated by commas, and the spaces around an item are not part of it;
- a rate is `<count>/<s|m|h|d>`, empty or `0` for no limit;
- a choice is one of its documented words, in any case;
- a secret may instead be given as `<NAME>_FILE`, the path of a file holding it, as Docker
  and Kubernetes secrets are; one trailing newline is not part of it.

A value that cannot be read is never a crash here: `read` falls back where the variable did
before (a boolean that is not one reads as false, as django-environ read it; a number that
is not one takes the default) and `problems` names it, which is what the start-up check
reports (`postulo.core.checks`). What cannot be resolved at all -- a secret given twice, a
file that cannot be read, a name and its old name both set -- is refused at once, naming the
variables and the path but never a value.
"""

from __future__ import annotations

import difflib
import enum
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from postulo.core import proxy

#: src/postulo/core/configuration.py -> src/postulo
PACKAGE_DIR = Path(__file__).resolve().parents[1]
#: ... -> the repository, or wherever the installation is
REPO_DIR = PACKAGE_DIR.parents[1]
DATA_DIR = REPO_DIR / "data"

#: The suffix of the variable that names a file holding a secret.
FILE_SUFFIX = "_FILE"


class Kind(enum.StrEnum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    SIZE = "size"
    DURATION = "duration"
    RATE = "rate"
    URL = "URL"
    LIST = "list"
    CHOICE = "choice"
    SECRET = "secret"  # noqa: S105 - the name of a kind, not a password
    TEXT = "text"
    PATH = "path"


class Scope(enum.StrEnum):
    """Who reads a variable, which decides where it is documented."""

    #: Postulo itself: its settings, or a command it runs.
    INSTANCE = "instance"
    #: The image's start-up script, `docker/entrypoint.sh`.
    CONTAINER = "container"
    #: Another program in the image, such as gunicorn.
    SERVER = "server"
    #: A build argument of the image: `docker build --build-arg`.
    BUILD = "build"
    #: The test suite, CI and the maintainers' scripts; never an instance.
    DEVELOPMENT = "development"


@dataclass(frozen=True)
class Alias:
    """An old name a variable is still read under, for one minor release, with a warning.

    `unit` is the unit a bare number under the old name is counted in, where the old name
    carried a different one (`_MB` for a variable now in bytes).
    """

    name: str
    unit: str = ""


@dataclass(frozen=True)
class Area:
    """A group of variables: a Server page, a part of the instance or of its making."""

    key: str
    title: str
    #: The words a name in this area may begin with, after `POSTULO_`.
    words: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class Variable:
    name: str
    kind: Kind
    area: str
    #: One plain line: what it does.
    description: str
    default: object = None
    #: For a size or a duration, the unit a bare number is counted in.
    unit: str = ""
    choices: tuple[str, ...] = ()
    #: A choice that may also be left empty.
    blank: bool = False
    minimum: int | None = None
    maximum: int | None = None
    #: Whether a change needs a restart. Everything read at start-up does.
    restart: bool = True
    scope: Scope = Scope.INSTANCE
    #: The `SiteSettings` field it pins, if any.
    field: str = ""
    #: The Django setting it ends up in, when not its own name.
    setting: str = ""
    #: The settings module that reads it.
    module: str = "base"
    #: A value to show in `.env.example` instead of the default.
    example: str = ""
    #: Whether `.env.example` sets it rather than showing it commented out.
    required: bool = False
    #: What a deployer should know beyond the one line.
    notes: str = ""
    #: The default, said in words, where it is not one value.
    default_text: str = ""
    #: What the image sets it to, where it does.
    image: str = ""
    #: The start-up check that already refuses a wrong value, so `problems` leaves it alone.
    checked_by: str = ""
    #: Read the way django-environ reads, with `$NAME` meaning another variable's value.
    interpolate: bool = True
    #: Set but empty counts as not set.
    empty_is_unset: bool = False
    #: For a boolean read by a shell script: the only spellings it understands.
    accepts: tuple[str, ...] = ()
    aliases: tuple[Alias, ...] = ()
    #: Whether `.env.example` carries it (a variable read before the file is, does not).
    in_env_file: bool = True

    @property
    def documented(self) -> bool:
        """Whether a deployer is told about it: everything an instance or its image reads."""
        return self.scope is not Scope.DEVELOPMENT


# ------------------------------------------------------------------------------------- areas

AREAS: tuple[Area, ...] = (
    Area(
        "server",
        "The server",
        ("SECRET", "DEBUG", "ALLOWED", "CSRF", "TRUSTED", "ADMIN", "PUBLIC", "SOURCE", "LEGAL"),
        notes="Required in production: POSTULO_SECRET_KEY and POSTULO_ALLOWED_HOSTS.",
    ),
    Area("database", "Database and cache", ("DATABASE", "CACHE", "RATELIMIT")),
    Area(
        "https",
        "HTTPS (production only)",
        ("SSL", "HSTS", "SECURE"),
        notes="Read by the production settings alone.",
    ),
    Area("sign-in", "Sign-in and registration", ("OIDC", "REGISTRATION", "EMAIL")),
    Area(
        "email",
        "Email",
        ("EMAIL",),
        notes=(
            "All of these can be set from Server settings -> Email instead, where they take "
            "effect without a restart and can be tested before they are saved. Set them here "
            "to pin them: the page then shows the value read-only and says which variable it "
            "came from."
        ),
    ),
    Area(
        "logs",
        "Logs, metrics and updates",
        ("LOG", "LOGS", "METRICS", "UPDATE"),
        notes=(
            "Make a token for /metrics or /logs with `openssl rand -hex 32`; one shorter than "
            "24 characters is reported at start-up, and ten wrong ones in an hour shut an "
            "address out."
        ),
    ),
    Area("defaults", "Defaults", ("TIME",)),
    Area(
        "storage",
        "Storage",
        ("MEDIA", "STATIC", "BACKUP", "UPLOAD", "FIELD"),
        notes="A relative default is under the installation; the image keeps it in /app/data.",
    ),
    Area("documents", "Documents", ("PDF",)),
    Area(
        "background",
        "Background work",
        ("BACKGROUND", "WORKER", "SCHEDULER", "EXPORT"),
    ),
    Area(
        "capture",
        "Capture",
        ("CAPTURE",),
        notes=(
            "Keeping pages and ignoring robots.txt can also be set from Server settings -> "
            "Capture; set them here to pin them."
        ),
    ),
    Area(
        "outbound",
        "What the server may dial",
        ("CONNECTIONS", "FETCH", "OUTBOUND"),
    ),
    Area(
        "maps",
        "Maps and places",
        ("MAP", "GEOCODER", "GEONAMES", "GEOLOCATIONS", "ESCO"),
        notes=(
            "The tiles and the geocoder can also be set from Server settings -> Maps when not "
            "set here."
        ),
    ),
    Area("plugins", "Plugins", ("PLUGINS", "PLUGIN")),
    Area(
        "limits",
        "Rate limits",
        (),
        notes=(
            "What one account may make the server do: <count>/<s|m|h|d>, such as 30/h; empty "
            "or 0 switches a limit off. A rate is named for what it bounds, so its name begins "
            "with that and not with an area."
        ),
    ),
    Area(
        "container",
        "The container",
        ("CONTAINER",),
        notes="Read by the image's start-up script and by the programs it starts.",
    ),
    Area(
        "image", "Building the image", ("IMAGE",), notes="Build arguments: --build-arg NAME=value."
    ),
    Area(
        "development",
        "Development",
        ("TEST", "SEED", "WIKI", "CATALOGUE"),
        notes="The test suite, CI and the maintainers' scripts read these; an instance never does.",
    ),
)

AREA_BY_KEY = {area.key: area for area in AREAS}
#: Every word a name may begin with, after `POSTULO_`.
AREA_WORDS = frozenset(word for area in AREAS for word in area.words)


# --------------------------------------------------------------------------------- variables


def _rate(name: str, default: str, description: str, notes: str = "") -> Variable:
    return Variable(
        name,
        Kind.RATE,
        "limits",
        description,
        default=default,
        notes=notes,
        checked_by="postulo.E002",
    )


_VARIABLES: tuple[Variable, ...] = (
    # ------------------------------------------------------------------------------ server
    Variable(
        "POSTULO_SECRET_KEY",
        Kind.SECRET,
        "server",
        "The key that signs sessions and derives the key stored credentials are encrypted with.",
        default=None,
        required=True,
        notes=(
            "Required in production, and refused if it is not a secret: at least 50 "
            "characters, at least 5 distinct ones, and not a placeholder. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(64))"\n'
            "Do not replace it on a running instance without reading Configuration first: set "
            "POSTULO_FIELD_KEY to the old one, or the stored credentials become unreadable."
        ),
        setting="SECRET_KEY",
    ),
    Variable(
        "POSTULO_DEBUG",
        Kind.BOOLEAN,
        "server",
        "Django's debug mode: detailed error pages. Never on a public instance.",
        default=False,
        default_text="false (true in the development settings; the production settings ignore it)",
        setting="DEBUG",
    ),
    Variable(
        "POSTULO_ALLOWED_HOSTS",
        Kind.LIST,
        "server",
        "The host names this instance answers to.",
        default=["localhost", "127.0.0.1"],
        required=True,
        example="localhost,127.0.0.1",
        notes=(
            "A '*' is refused in production: it accepts any Host header. To start anyway while "
            "you plan the change, set POSTULO_ALLOW_WILDCARD_HOSTS=true."
        ),
        setting="ALLOWED_HOSTS",
    ),
    Variable(
        "POSTULO_ALLOW_WILDCARD_HOSTS",
        Kind.BOOLEAN,
        "https",
        "Start in production even though POSTULO_ALLOWED_HOSTS contains '*'.",
        default=False,
        module="prod",
    ),
    Variable(
        "POSTULO_CSRF_TRUSTED_ORIGINS",
        Kind.LIST,
        "server",
        "Origins a form may be posted from, needed behind a reverse proxy on a real domain.",
        default=[],
        example="https://postulo.example.org",
        setting="CSRF_TRUSTED_ORIGINS",
    ),
    Variable(
        "POSTULO_TRUSTED_PROXIES",
        Kind.LIST,
        "server",
        "The addresses or networks a reverse proxy may be at; forwarding headers from "
        "anywhere else are dropped.",
        default=list(proxy.DEFAULT_TRUSTED_PROXIES),
        example="172.18.0.0/16",
        notes=(
            "Postulo believes X-Forwarded-Proto and X-Forwarded-For only from these, so an "
            "instance published straight onto a port cannot be told by a stranger that their "
            "request arrived over HTTPS, and nobody can choose the address the sign-in limits "
            "count them under. A proxy on the host reaching the container arrives from the "
            "Docker network's gateway; a proxy container in the same Compose project arrives "
            "from that network. Server settings -> Overview shows the address the current "
            "request came from, which is what to name here. Set it to nothing at all if there "
            "is no proxy. The image gives gunicorn the same list."
        ),
    ),
    Variable(
        "POSTULO_ADMIN_URL",
        Kind.TEXT,
        "server",
        "The path Django's admin is mounted at; empty, the admin does not exist.",
        default="",
        example="some-private-path/",
        notes=(
            "Server settings covers people, sign-in policy, plugins, email, logs and defaults "
            "without it; an admin nobody mounted is one nobody can guess at. It has no login of "
            "its own: you sign in to Postulo and are taken there."
        ),
    ),
    Variable(
        "POSTULO_PUBLIC_URL",
        Kind.URL,
        "server",
        "Where this instance is reached from outside, for links in messages sent with no "
        "request around.",
        default="",
        example="https://postulo.example.org",
        checked_by="postulo.E003",
    ),
    Variable(
        "POSTULO_SOURCE_URL",
        Kind.URL,
        "server",
        "Where this instance's code can be had, linked as Source code at the foot of every page.",
        default="https://source.tiagoagueda.com/postulo/postulo",
        notes=(
            "The AGPL entitles the people using a modified Postulo you run as a service to its "
            "source, and this is where that offer points: set it to wherever your code lives. "
            "Unset, it is the upstream repository."
        ),
        checked_by="postulo.E006",
    ),
    Variable(
        "POSTULO_LEGAL_NOTICE_URL",
        Kind.URL,
        "server",
        "Your legal notice (an imprint or a contact page), linked at the foot of every page.",
        default="",
        notes="Some countries require one. No link at all when unset.",
        checked_by="postulo.E007",
    ),
    # ---------------------------------------------------------------------------- database
    Variable(
        "POSTULO_DATABASE_URL",
        Kind.URL,
        "database",
        "The database, as a URL.",
        default=f"sqlite:///{DATA_DIR / 'postulo.sqlite3'}",
        default_text="SQLite at data/postulo.sqlite3",
        example="postgres://postulo@localhost:5432/postulo",
        notes="sqlite:///path or postgres://user@host:port/name.",
    ),
    Variable(
        "POSTULO_DATABASE_PASSWORD",
        Kind.SECRET,
        "database",
        "The database's password, for one a URL cannot carry.",
        default="",
        interpolate=False,
        empty_is_unset=True,
        notes=(
            "A password with a `/`, `?` or `%` in it cannot be written into the URL. Leave it "
            "out of the URL and give it here instead, exactly as it is. It wins over "
            "POSTGRES_PASSWORD where both are set."
        ),
    ),
    Variable(
        "POSTGRES_PASSWORD",
        Kind.SECRET,
        "database",
        "The PostgreSQL container's password, read by Postulo too when the URL has none.",
        default="",
        interpolate=False,
        empty_is_unset=True,
        notes=(
            "With the PostgreSQL compose file use this one: it passes this same file to its "
            "database container, which understands only this name, so one line serves both. "
            "Mind that it gives the database container this whole file, the secret keys "
            "included; the threat model says why that is accepted."
        ),
    ),
    Variable(
        "POSTULO_CACHE_URL",
        Kind.URL,
        "database",
        "The cache, as a URL; by default a table in the database.",
        default="dbcache://postulo_cache",
        example="redis://localhost:6379/1",
        notes=(
            "Postulo keeps things there that must be counted the same way by every worker -- "
            "how many times a password has been got wrong, above all -- so the default is "
            "shared and survives a restart. Redis or Memcached are faster; anything else makes "
            "those counts per worker again."
        ),
    ),
    Variable(
        "POSTULO_CACHE_MAX_ENTRIES",
        Kind.INTEGER,
        "database",
        "How many entries the database cache holds before it starts to cull.",
        default=200_000,
        minimum=1,
        notes=(
            "The rate limits and the sign-in lockout live in it, and a cull deletes keys in "
            "alphabetical order, not the oldest, so the default is far past what honest traffic "
            "reaches. Not used with Redis or Memcached."
        ),
    ),
    Variable(
        "POSTULO_RATELIMIT_DIR",
        Kind.PATH,
        "database",
        "Where Postulo's own allowances are counted, in files, with the database cache.",
        default=DATA_DIR / "ratelimit",
        notes="Outside any request's transaction, so a charge survives a view that failed.",
    ),
    # ------------------------------------------------------------------------------- https
    Variable(
        "POSTULO_ALLOW_WEAK_SECRET_KEY",
        Kind.BOOLEAN,
        "https",
        "Start in production with a secret key that is too weak, while you plan replacing it.",
        default=False,
        module="prod",
    ),
    Variable(
        "POSTULO_SSL_REDIRECT",
        Kind.BOOLEAN,
        "https",
        "Redirect plain HTTP to HTTPS.",
        default=True,
        module="prod",
        setting="SECURE_SSL_REDIRECT",
    ),
    Variable(
        "POSTULO_HSTS_SECONDS",
        Kind.DURATION,
        "https",
        "How long a browser is told to use HTTPS only.",
        default=31_536_000,
        unit="seconds",
        minimum=0,
        module="prod",
        setting="SECURE_HSTS_SECONDS",
    ),
    Variable(
        "POSTULO_HSTS_INCLUDE_SUBDOMAINS",
        Kind.BOOLEAN,
        "https",
        "Whether that applies to every subdomain too.",
        default=True,
        module="prod",
        setting="SECURE_HSTS_INCLUDE_SUBDOMAINS",
    ),
    Variable(
        "POSTULO_HSTS_PRELOAD",
        Kind.BOOLEAN,
        "https",
        "Ask to be put on the browsers' preload list, which is close to irreversible.",
        default=False,
        module="prod",
        setting="SECURE_HSTS_PRELOAD",
    ),
    Variable(
        "POSTULO_SECURE_COOKIES",
        Kind.BOOLEAN,
        "https",
        "Send cookies over HTTPS only.",
        default=True,
        module="prod",
        notes=(
            "Turn it off only for an instance reached solely inside a mesh VPN (NetBird, "
            "Tailscale), where the browser sees plain HTTP but the wire is already encrypted, "
            "and turn POSTULO_SSL_REDIRECT off with it."
        ),
    ),
    # ----------------------------------------------------------------------------- sign-in
    Variable(
        "POSTULO_REGISTRATION_OPEN",
        Kind.BOOLEAN,
        "sign-in",
        "Whether anybody may sign up; off, the instance is invite-only.",
        default=False,
        field="registration_open",
    ),
    Variable(
        "POSTULO_EMAIL_CODE_TIMEOUT",
        Kind.DURATION,
        "sign-in",
        "How long a sign-in code sent by email stays valid.",
        default=180,
        unit="seconds",
        minimum=1,
        setting="ACCOUNT_LOGIN_BY_CODE_TIMEOUT",
        notes=(
            "Signing in with a code sent by email is an administrator's choice under Server "
            "settings -> Sign-in. Three minutes is long enough for mail to arrive and short "
            "enough that a code left in an inbox stops being a key."
        ),
    ),
    Variable(
        "POSTULO_EMAIL_CODE_ATTEMPTS",
        Kind.INTEGER,
        "sign-in",
        "How many wrong tries one sign-in code allows.",
        default=3,
        minimum=1,
        setting="ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS",
    ),
    Variable(
        "POSTULO_EMAIL_CODE_RESENDS",
        Kind.INTEGER,
        "sign-in",
        "How many times one sign-in code may be sent again.",
        default=3,
        minimum=0,
        setting="ACCOUNT_LOGIN_BY_CODE_MAX_RESEND_COUNT",
        notes="Bounded because each is an email somebody may not have asked for.",
    ),
    Variable(
        "POSTULO_OIDC_NAME",
        Kind.TEXT,
        "sign-in",
        "The name on the single sign-on button.",
        default="Single sign-on",
    ),
    Variable(
        "POSTULO_OIDC_SERVER_URL",
        Kind.URL,
        "sign-in",
        "The OpenID Connect provider's address; with the client ID, turns single sign-on on.",
        default="",
        example="https://id.example.org/realms/postulo",
    ),
    Variable(
        "POSTULO_OIDC_CLIENT_ID",
        Kind.TEXT,
        "sign-in",
        "The client ID Postulo has at the provider.",
        default="",
    ),
    Variable(
        "POSTULO_OIDC_CLIENT_SECRET",
        Kind.SECRET,
        "sign-in",
        "The client secret Postulo has at the provider.",
        default="",
    ),
    Variable(
        "POSTULO_OIDC_AUTO_SIGNUP",
        Kind.BOOLEAN,
        "sign-in",
        "Whether somebody new arriving through the provider gets an account.",
        default=False,
        notes=(
            "Off, single sign-on signs in accounts that exist; the provider is not an invitation."
        ),
    ),
    Variable(
        "POSTULO_OIDC_LINK_BY_EMAIL",
        Kind.BOOLEAN,
        "sign-in",
        "Whether an address the provider has verified signs somebody in to the local account "
        "holding it.",
        default=False,
        notes=(
            "On, single sign-on just works and nobody connects anything by hand. It also means "
            "this instance takes the provider's word that the person proved they hold that "
            "address -- true of a Keycloak or Authentik you run, not automatically true of every "
            "endpoint that speaks OpenID Connect. Off, a person signs in here once and connects "
            "the provider from their own account page. With a provider and this unset, start-up "
            "says so."
        ),
    ),
    Variable(
        "POSTULO_OIDC_IS_SECOND_FACTOR",
        Kind.BOOLEAN,
        "sign-in",
        "Whether arriving through the provider counts as the second factor.",
        default=False,
        field="sso_is_second_factor",
        notes=(
            "Off, because Postulo cannot see how the provider checked anybody: that is your "
            "judgement about your own provider. It never applies to a password sign-in and it "
            "removes nobody's app. A passkey already counts on its own. Also settable under "
            "Server settings -> Sign-in."
        ),
    ),
    # ------------------------------------------------------------------------------- email
    Variable(
        "POSTULO_DEFAULT_FROM_EMAIL",
        Kind.TEXT,
        "email",
        "The address Postulo's own messages are sent from.",
        default="postulo@localhost",
        example="postulo@example.org",
        field="email_from",
        setting="DEFAULT_FROM_EMAIL",
    ),
    Variable(
        "POSTULO_EMAIL_HOST",
        Kind.TEXT,
        "email",
        "The mail server.",
        default="localhost",
        example="smtp.example.org",
        field="email_host",
    ),
    Variable(
        "POSTULO_EMAIL_PORT",
        Kind.INTEGER,
        "email",
        "The mail server's port.",
        default=25,
        example="587",
        minimum=1,
        maximum=65_535,
        field="email_port",
    ),
    Variable(
        "POSTULO_EMAIL_HOST_USER",
        Kind.TEXT,
        "email",
        "The user name at the mail server.",
        default="",
        field="email_username",
    ),
    Variable(
        "POSTULO_EMAIL_HOST_PASSWORD",
        Kind.SECRET,
        "email",
        "The password at the mail server.",
        default="",
        field="email_password",
    ),
    Variable(
        "POSTULO_EMAIL_SECURITY",
        Kind.CHOICE,
        "email",
        "How the connection to the mail server is encrypted.",
        default="starttls",
        choices=("none", "starttls", "ssl"),
        default_text="starttls (none when POSTULO_EMAIL_USE_TLS is false)",
        field="email_security",
        checked_by="postulo.E001",
        notes=(
            "starttls is usually port 587 and ssl port 465. They are not interchangeable: point "
            "one at the other's port and each waits for the other to speak, which looks exactly "
            "like the server being down."
        ),
    ),
    Variable(
        "POSTULO_EMAIL_USE_TLS",
        Kind.BOOLEAN,
        "email",
        "The older switch POSTULO_EMAIL_SECURITY replaces: true is starttls, false is none.",
        default=True,
        field="email_security",
        notes=(
            "Still honoured, so an existing .env goes on meaning what it meant; it cannot "
            "express ssl, which is why POSTULO_EMAIL_SECURITY exists."
        ),
    ),
    Variable(
        "POSTULO_EMAIL_TIMEOUT",
        Kind.DURATION,
        "email",
        "How long to wait for the mail server.",
        default=10,
        unit="seconds",
        minimum=1,
        field="email_timeout",
    ),
    Variable(
        "POSTULO_EMAIL_AUTH",
        Kind.CHOICE,
        "email",
        "How the mail server is told who is connecting.",
        default="password",
        choices=("password", "xoauth2"),
        field="email_auth",
        checked_by="postulo.E001",
        notes=(
            "A relay of your own, Mailcow, Postfix, or Gmail with an app password: leave this "
            "alone. Microsoft 365 stops accepting passwords for SMTP by default at the end of "
            "December 2026; after that it has to be xoauth2. See Configuration -> Email in the "
            "wiki."
        ),
    ),
    Variable(
        "POSTULO_EMAIL_OAUTH_PROVIDER",
        Kind.CHOICE,
        "email",
        "With xoauth2: whose sign-in the token comes from.",
        default="",
        choices=("microsoft", "google"),
        blank=True,
        field="email_oauth_provider",
        checked_by="postulo.E008",
    ),
    Variable(
        "POSTULO_EMAIL_OAUTH_GRANT",
        Kind.CHOICE,
        "email",
        "With xoauth2: application (the application sends on its own) or mailbox (signed in "
        "once as the mailbox).",
        default="",
        choices=("mailbox", "application"),
        blank=True,
        field="email_oauth_grant",
        checked_by="postulo.E008",
    ),
    Variable(
        "POSTULO_EMAIL_OAUTH_TENANT",
        Kind.TEXT,
        "email",
        "With Microsoft: the directory (tenant) ID.",
        default="",
        field="email_oauth_tenant",
    ),
    Variable(
        "POSTULO_EMAIL_OAUTH_CLIENT_ID",
        Kind.TEXT,
        "email",
        "With xoauth2: the client ID registered with the provider.",
        default="",
        field="email_oauth_client_id",
    ),
    Variable(
        "POSTULO_EMAIL_OAUTH_CLIENT_SECRET",
        Kind.SECRET,
        "email",
        "With xoauth2: the client secret registered with the provider.",
        default="",
        field="email_oauth_client_secret",
    ),
    # -------------------------------------------------------------------------------- logs
    Variable(
        "POSTULO_LOG_DIR",
        Kind.PATH,
        "logs",
        "Where Postulo keeps its own log, to be read under Server settings; empty keeps none.",
        default=str(DATA_DIR / "logs"),
        notes=("Everything still goes to the console as well, so `docker logs` is unaffected."),
    ),
    Variable(
        "POSTULO_LOG_MAX_BYTES",
        Kind.SIZE,
        "logs",
        "How large one log file grows before the next is started.",
        default=5 * 1024 * 1024,
        unit="bytes",
        minimum=0,
    ),
    Variable(
        "POSTULO_LOG_BACKUPS",
        Kind.INTEGER,
        "logs",
        "How many full log files are kept behind the current one.",
        default=3,
        minimum=0,
        notes="About 20 MB in all with the defaults.",
    ),
    Variable(
        "POSTULO_LOG_FORMAT",
        Kind.CHOICE,
        "logs",
        "What the console gets: simple, a line a person reads, or json, for a collector.",
        default="simple",
        choices=("simple", "json"),
        checked_by="postulo.E001",
        notes="The file is always JSON.",
    ),
    Variable(
        "POSTULO_LOG_LEVEL",
        Kind.TEXT,
        "logs",
        "The least serious record written: DEBUG, INFO, WARNING, ERROR or CRITICAL.",
        default="INFO",
    ),
    Variable(
        "POSTULO_LOGS_ENDPOINT_ENABLED",
        Kind.BOOLEAN,
        "logs",
        "Serve the log at /logs for a collector to scrape.",
        default=False,
        notes=(
            "Off, and a 404 when off, so nothing confirms the endpoint is there. On, it needs "
            "a token: with no token it refuses to serve. Unlike metrics, a log entry names "
            "connections, companies and applications, so this is personal data leaving the "
            "instance."
        ),
    ),
    Variable(
        "POSTULO_LOGS_TOKEN",
        Kind.SECRET,
        "logs",
        "The token a collector sends to read /logs.",
        default="",
    ),
    Variable(
        "POSTULO_METRICS_ENABLED",
        Kind.BOOLEAN,
        "logs",
        "Serve Prometheus metrics at /metrics.",
        default=False,
        notes=(
            "Off, and a 404 when off. They carry nothing about anybody: counts of records, work "
            "waiting, and what has failed. With no token they are readable by whoever can reach "
            "the instance, which Server settings -> Logs says plainly."
        ),
    ),
    Variable(
        "POSTULO_METRICS_TOKEN",
        Kind.SECRET,
        "logs",
        "The token a scraper sends to read /metrics.",
        default="",
    ),
    Variable(
        "POSTULO_UPDATE_CHECK",
        Kind.BOOLEAN,
        "logs",
        "Let the scheduler ask once a day whether a newer Postulo exists.",
        default=False,
        notes=(
            "One request to the address below and to nothing else, never from a page. Off, "
            "Server settings -> Overview cannot say whether the version it shows is the newest. "
            "Postulo makes no request on your behalf unless you say so, which is why this is off."
        ),
    ),
    Variable(
        "POSTULO_UPDATE_SOURCE",
        Kind.URL,
        "logs",
        "Where the update check asks.",
        default="https://source.tiagoagueda.com/api/v1/repos/postulo/postulo/releases/latest",
    ),
    # ---------------------------------------------------------------------------- defaults
    Variable(
        "POSTULO_TIME_ZONE",
        Kind.TEXT,
        "defaults",
        "The instance's time zone, an IANA name such as Europe/Paris.",
        default="Europe/Paris",
        field="default_time_zone",
        setting="TIME_ZONE",
    ),
    # ----------------------------------------------------------------------------- storage
    Variable(
        "POSTULO_MEDIA_ROOT",
        Kind.PATH,
        "storage",
        "Where uploaded documents are kept. Never served directly.",
        default=DATA_DIR / "media",
        image="/app/data/media",
        setting="MEDIA_ROOT",
    ),
    Variable(
        "POSTULO_STATIC_ROOT",
        Kind.PATH,
        "storage",
        "Where collectstatic writes the stylesheets, scripts and pictures.",
        default=REPO_DIR / "staticfiles",
        image="/app/staticfiles",
        setting="STATIC_ROOT",
    ),
    Variable(
        "POSTULO_MEDIA_ACCEL_PREFIX",
        Kind.TEXT,
        "storage",
        "Hand a download to nginx at this internal location instead of streaming it.",
        default="",
        example="/protected-media/",
    ),
    Variable(
        "POSTULO_MEDIA_SENDFILE",
        Kind.BOOLEAN,
        "storage",
        "Hand a download to Apache's mod_xsendfile instead of streaming it.",
        default=False,
    ),
    Variable(
        "POSTULO_MEDIA_SWEEP",
        Kind.BOOLEAN,
        "storage",
        "Let the scheduler remove a file under the media root that no record names.",
        default=True,
        notes="Off, that is left to `manage.py prune_media`.",
    ),
    Variable(
        "POSTULO_MEDIA_SWEEP_GRACE_HOURS",
        Kind.DURATION,
        "storage",
        "How old such a file must be before it is removed.",
        default=24,
        unit="hours",
        minimum=0,
    ),
    Variable(
        "POSTULO_SCRUB_EVERY_DAYS",
        Kind.DURATION,
        "storage",
        "How long the scheduler leaves an uploaded file it has checked before reading it again.",
        default=7,
        unit="days",
        minimum=0,
        notes=(
            "Each file is read against the size and checksum recorded when it arrived, a few on "
            "every pass. 0 reads them again on every pass. `manage.py verify_media` reads all "
            "of them at once."
        ),
    ),
    Variable(
        "POSTULO_BACKUP_DIR",
        Kind.PATH,
        "storage",
        "Where `manage.py backup` and Server settings -> Backups write.",
        default=DATA_DIR / "backups",
        image="/app/data/backups",
        notes="Move it elsewhere if the data volume is the thing being backed up.",
    ),
    Variable(
        "POSTULO_BACKUP_UPLOAD_MAX_MB",
        Kind.SIZE,
        "storage",
        "The largest archive Server settings -> Backups accepts for a restore.",
        default=512,
        unit="megabytes",
        minimum=1,
        notes="A reverse proxy in front has its own limit, which has to be raised to match.",
    ),
    Variable(
        "POSTULO_UPLOAD_CEILING_MB",
        Kind.SIZE,
        "storage",
        "The largest request body any page but the backup and plugin uploads accepts.",
        default=25,
        unit="megabytes",
        minimum=1,
        notes=(
            "A longer one is refused with 413 before it is read. Also set client_max_body_size "
            "at the proxy."
        ),
    ),
    Variable(
        "POSTULO_BACKUP_DOWNLOAD",
        Kind.CHOICE,
        "storage",
        "Who may download a backup through the browser.",
        default="administrators",
        choices=("administrators", "operator", "console"),
        checked_by="postulo.E001",
        notes=(
            "administrators is every administrator, operator only the superuser, and console "
            "nobody: the files are taken from the data volume."
        ),
    ),
    Variable(
        "POSTULO_FIELD_KEY",
        Kind.SECRET,
        "storage",
        "A key of its own for the stored connection credentials, instead of one derived from "
        "POSTULO_SECRET_KEY.",
        default="",
        notes="Set it to the old secret key before replacing POSTULO_SECRET_KEY.",
    ),
    # --------------------------------------------------------------------------- documents
    Variable(
        "POSTULO_PDF_BACKEND",
        Kind.CHOICE,
        "documents",
        "What draws a PDF.",
        default="auto",
        choices=("auto", "weasyprint", "chromium"),
        checked_by="postulo.E001",
        notes=(
            "WeasyPrint is the default and ships with Postulo; auto prefers it and falls back to "
            "Chromium where its system libraries are missing. PDF export is optional: without a "
            "working renderer everything else still works."
        ),
    ),
    # -------------------------------------------------------------------------- background
    Variable(
        "POSTULO_BACKGROUND_WORK",
        Kind.BOOLEAN,
        "background",
        "Send the slow work to a worker instead of doing it while the person waits.",
        default=False,
        notes=(
            "Fetching a posting's page, finding a logo, rendering a PDF, building an export "
            "archive, telling notifiers. Off by default: one container is the recommended shape "
            "for a personal instance, and a queue nobody empties is a button that does nothing. "
            "Set it together with starting the worker, never separately:\n"
            "  docker compose -f docker/compose.yml --profile worker up -d\n"
            "or, on PostgreSQL, with the file you started with:\n"
            "  docker compose -f docker/compose.postgres.yml --profile worker up -d"
        ),
    ),
    Variable(
        "POSTULO_WORKER_HEARTBEAT",
        Kind.PATH,
        "background",
        "The file the worker touches as it goes round.",
        default=DATA_DIR / "worker-heartbeat",
    ),
    Variable(
        "POSTULO_SCHEDULER_HEARTBEAT",
        Kind.PATH,
        "background",
        "The file the scheduler touches at the end of each pass.",
        default=DATA_DIR / "scheduler-heartbeat",
    ),
    Variable(
        "POSTULO_EXPORT_KEEP_HOURS",
        Kind.DURATION,
        "background",
        "How long a built export archive waits to be downloaded before it is deleted.",
        default=24,
        unit="hours",
        minimum=0,
    ),
    # ----------------------------------------------------------------------------- capture
    Variable(
        "POSTULO_CAPTURE_IGNORE_ROBOTS",
        Kind.BOOLEAN,
        "capture",
        "Fetch a posting even where robots.txt asks not to.",
        default=False,
        field="capture_ignore_robots",
        notes="On, you are responsible for the requests your instance makes.",
    ),
    Variable(
        "POSTULO_CAPTURE_KEEP_SOURCE",
        Kind.BOOLEAN,
        "capture",
        "Whether a capture may keep the source of the page it was read from.",
        default=False,
        field="capture_keep_source",
        notes=(
            "Off, because keeping a copy of somebody else's page is a decision to make on "
            "purpose. This is the instance's half: each person switches it on for their own "
            "captures under Settings -> Capture, and a no here is final."
        ),
    ),
    Variable(
        "POSTULO_CAPTURE_KEEP_RENDERING",
        Kind.BOOLEAN,
        "capture",
        "Whether a capture may keep a rendering of the whole page.",
        default=False,
        field="capture_keep_rendering",
    ),
    Variable(
        "POSTULO_CAPTURE_SOURCE_MAX_BYTES",
        Kind.SIZE,
        "capture",
        "What one kept source may weigh, refused before it is read.",
        default=2_000_000,
        unit="bytes",
        checked_by="postulo.E004",
    ),
    Variable(
        "POSTULO_CAPTURE_RENDERING_MAX_BYTES",
        Kind.SIZE,
        "capture",
        "What one kept rendering may weigh, refused before it is read.",
        default=10 * 1024 * 1024,
        unit="bytes",
        field="capture_rendering_max_mb",
        checked_by="postulo.E004",
    ),
    Variable(
        "POSTULO_CAPTURE_ACCOUNT_MAX_BYTES",
        Kind.SIZE,
        "capture",
        "What one account's kept pages may weigh together.",
        default=1024 * 1024 * 1024,
        unit="bytes",
        checked_by="postulo.E004",
    ),
    Variable(
        "POSTULO_CAPTURE_PAGE_KEEP_DAYS",
        Kind.DURATION,
        "capture",
        "How long the page of a capture that never became a listing is kept; 0 keeps them all.",
        default=30,
        unit="days",
        checked_by="postulo.E005",
    ),
    # ---------------------------------------------------------------------------- outbound
    Variable(
        "POSTULO_CONNECTIONS_ALLOW_PRIVATE",
        Kind.BOOLEAN,
        "outbound",
        "Whether a connection (a Paperless on your network, a mail relay) may reach a private "
        "or local address.",
        default=False,
        notes=(
            "It is one switch for the whole instance: on, every account can aim a webhook or an "
            "own-mail connection at any address the server can reach and read what the Test "
            "button says back. Leave it off on an instance with more than one person; there is "
            "no per-address allowlist."
        ),
    ),
    Variable(
        "POSTULO_FETCH_PORTS",
        Kind.LIST,
        "outbound",
        "The ports a fetch of something public (a posting, a link check, a logo) may dial.",
        default=["80", "443", "8080", "8443"],
        notes=(
            "The ordinary web ports, so an account cannot use the server to probe other ports "
            "of a public host. A connection is not limited by this list. * lifts the limit."
        ),
    ),
    Variable(
        "POSTULO_OUTBOUND_USE_ENV_PROXY",
        Kind.BOOLEAN,
        "outbound",
        "Whether outbound requests honour HTTP_PROXY, HTTPS_PROXY and NO_PROXY.",
        default=False,
        notes=(
            "A proxy tunnel verifies the certificate against the address the destination check "
            "pinned, not the name. Only for an instance whose sole way out is a proxy."
        ),
    ),
    # -------------------------------------------------------------------------------- maps
    Variable(
        "POSTULO_MAP_TILES",
        Kind.CHOICE,
        "maps",
        "Where the companies' map takes its tiles: OpenStreetMap, your own, or none.",
        default="osm",
        choices=("osm", "custom", "none"),
        field="map_tiles",
        checked_by="postulo.E001",
        notes=(
            "osm is OpenStreetMap's public tiles, fetched by the viewer's browser; custom the "
            "address below; none the world's outline, nothing fetched."
        ),
    ),
    Variable(
        "POSTULO_MAP_TILE_URL",
        Kind.URL,
        "maps",
        "With custom tiles: the tile address, https:// or a path on this instance.",
        default="",
        example="https://tiles.example.org/{z}/{x}/{y}.png",
        field="map_tile_url",
    ),
    Variable(
        "POSTULO_MAP_TILE_ATTRIBUTION",
        Kind.TEXT,
        "maps",
        "With custom tiles: the credit shown on the map.",
        default="",
        field="map_tile_attribution",
    ),
    Variable(
        "POSTULO_GEOCODER",
        Kind.CHOICE,
        "maps",
        "Whether an address with a street is pinned at that street, and by which service.",
        default="",
        choices=("off", "nominatim", "photon", "addok"),
        blank=True,
        field="geocoder",
        default_text="off",
        notes=(
            "Set here, the choice is your word that the service may be sent the addresses on "
            "the map, people's and contacts' included."
        ),
    ),
    Variable(
        "POSTULO_GEOCODER_URL",
        Kind.URL,
        "maps",
        "The geocoding service's base address.",
        default="",
        field="geocoder_url",
    ),
    Variable(
        "POSTULO_GEOLOCATIONS_DIR",
        Kind.PATH,
        "maps",
        "Where the GeoNames tables the map places locations from are read and kept.",
        default=PACKAGE_DIR / "jobs" / "data",
        image="/app/data/geonames",
        interpolate=False,
        empty_is_unset=True,
        notes=(
            "The container fetches the city table here on first start; what it keeps is the "
            "lookup built from it (geonames-cities1000.sqlite) and the country table."
        ),
    ),
    Variable(
        "POSTULO_GEONAMES_POSTCODES",
        Kind.BOOLEAN,
        "maps",
        "Also fetch the GeoNames postal codes, so an address is placed at its postcode.",
        default=False,
        interpolate=False,
    ),
    Variable(
        "POSTULO_SKIP_GEONAMES",
        Kind.BOOLEAN,
        "container",
        "1 stops the container fetching the GeoNames city tables on its first start.",
        default=False,
        scope=Scope.CONTAINER,
        accepts=("0", "1"),
        notes=(
            "The download (a few megabytes, from download.geonames.org) is the one request made "
            "with nobody asking for it. Skipped, put geonames-cities1000.txt and "
            "geonames-countryinfo.txt in POSTULO_GEOLOCATIONS_DIR by hand to have the map anyway."
        ),
    ),
    Variable(
        "POSTULO_ESCO_DIR",
        Kind.PATH,
        "maps",
        "Where the ESCO classification of occupations and skills is read from.",
        default=PACKAGE_DIR / "jobs" / "data",
        image="/app/data/esco",
        interpolate=False,
        empty_is_unset=True,
    ),
    # ----------------------------------------------------------------------------- plugins
    Variable(
        "POSTULO_PLUGINS_DIR",
        Kind.PATH,
        "plugins",
        "Where plugins installed through the interface live.",
        default=DATA_DIR / "plugins",
        image="/app/data/plugins",
    ),
    Variable(
        "POSTULO_PLUGIN_CATALOGUES",
        Kind.TEXT,
        "plugins",
        "Catalogues an administrator may install plugins from, as name|url|public-key entries "
        "separated by commas.",
        default="",
        notes="Empty: an index is a list of code to run, so pointing at one is a decision.",
    ),
    # ------------------------------------------------------------------------- rate limits
    _rate(
        "POSTULO_CAPTURE_RATE",
        "30/h",
        "Captures one account may make.",
        notes=(
            "The tightest, because it is the only one that makes this server fetch an address "
            "somebody else chose. A capture that arrives with its own page source is not counted."
        ),
    ),
    _rate(
        "POSTULO_FETCH_RATE",
        "300/h",
        "Other fetches of an address somebody typed: link checks, logo addresses, Find logo.",
        notes="Counted once per address, so Check all on forty links spends forty.",
    ),
    _rate("POSTULO_WIKIDATA_RATE", "60/h", "Fetch from Wikidata presses, per account."),
    _rate("POSTULO_LOOKUP_RATE", "20/h", "DOI and ORCID look-ups together, per account."),
    _rate(
        "POSTULO_REGISTER_LOOKUP_RATE",
        "30/h",
        "Look-ups in a company register, per account.",
    ),
    _rate("POSTULO_API_RATE", "600/h", "Calls one API token may make."),
    _rate(
        "POSTULO_API_ACCOUNT_RATE",
        "3000/h",
        "Calls every token of one account may make together.",
    ),
    _rate(
        "POSTULO_API_TOKEN_RATE",
        "10/h",
        "API tokens one account may make; each is a way in with no second factor.",
    ),
    _rate("POSTULO_OUTBOX_RATE", "60/h", "Messages one account may send as itself."),
    _rate(
        "POSTULO_ENDPOINT_RATE",
        "120/h",
        "Reads of /logs and /metrics, per calling address.",
    ),
    _rate(
        "POSTULO_CONFIRMATION_RATE",
        "5/h",
        "Confirmations one account may ask to be sent again, across every kind of contact.",
    ),
    _rate(
        "POSTULO_NUMBER_RATE",
        "20/h",
        "How often one account may be told a telephone number is already recorded here.",
    ),
    _rate("POSTULO_TEXT_RATE", "5/h", "Text messages, per account."),
    _rate(
        "POSTULO_TEXT_PER_NUMBER_RATE",
        "3/h",
        "Text messages, per receiving number.",
        notes="A stranger whose number was mistyped into a form has no way to make it stop.",
    ),
    _rate(
        "POSTULO_RECOVERY_RATE",
        "10/h",
        "Recovery links one administrator may issue.",
    ),
    _rate(
        "POSTULO_COMPANY_NAME_RATE",
        "20/h",
        "How often one account may be told its sharer already has a company by a name.",
    ),
    _rate("POSTULO_USERNAME_RATE", "10/h", "Usernames one account may try to change to."),
    _rate(
        "POSTULO_SHARE_LOOKUP_RATE",
        "20/h",
        "People one account may look up on a record's Sharing page.",
    ),
    _rate(
        "POSTULO_CONNECTION_TEST_RATE",
        "10/h",
        "Tests of a connection, or of the mail settings, per account.",
    ),
    _rate("POSTULO_CONNECTION_SYNC_RATE", "20/h", "Sync now presses, per account."),
    _rate(
        "POSTULO_CONNECTION_PAIRING_RATE",
        "120/h",
        "Questions to a sync connection's plugin from its Pairing page, per account.",
    ),
    _rate(
        "POSTULO_SUGGESTION_RATE",
        "600/h",
        "Suggestions the skill box asks for as somebody types, per account.",
    ),
    # --------------------------------------------------------------------------- container
    Variable(
        "POSTULO_SKIP_MIGRATE",
        Kind.BOOLEAN,
        "container",
        "1 stops the container applying migrations as it starts.",
        default=False,
        scope=Scope.CONTAINER,
        accepts=("0", "1"),
        notes=(
            "The compose files set it on the scheduler and the worker, which start after the "
            "web container."
        ),
    ),
    Variable(
        "POSTULO_SKIP_PLUGIN_SYNC",
        Kind.BOOLEAN,
        "container",
        "1 stops the container installing again the plugins the data volume lists.",
        default=False,
        scope=Scope.CONTAINER,
        accepts=("0", "1"),
    ),
    Variable(
        "DJANGO_SETTINGS_MODULE",
        Kind.TEXT,
        "container",
        "Which settings module Postulo runs with.",
        default="postulo.config.settings.dev",
        default_text="postulo.config.settings.dev from manage.py, prod from the WSGI and ASGI "
        "entry points",
        image="postulo.config.settings.prod",
        scope=Scope.SERVER,
        interpolate=False,
        in_env_file=False,
    ),
    Variable(
        "GUNICORN_CMD_ARGS",
        Kind.TEXT,
        "container",
        "gunicorn's own options, read as if typed on its command line.",
        default="",
        image="--timeout 120 --max-requests 500 --max-requests-jitter 50",
        scope=Scope.SERVER,
        notes=(
            "Setting it replaces the image's whole string, so repeat anything you want to keep. "
            "The bind address, the worker count and the log destinations are in the image's "
            "command and are not affected. Two minutes rather than gunicorn's thirty seconds: "
            "drawing a PDF on a small machine can take longer. See Configuration in the wiki."
        ),
    ),
    Variable(
        "FORWARDED_ALLOW_IPS",
        Kind.LIST,
        "container",
        "The addresses gunicorn believes forwarding headers from.",
        default=[],
        default_text="POSTULO_TRUSTED_PROXIES",
        scope=Scope.SERVER,
        notes="The container gives gunicorn POSTULO_TRUSTED_PROXIES unless you set this.",
    ),
    # ------------------------------------------------------------------------------- image
    Variable(
        "POSTULO_EXTRA_PACKAGES",
        Kind.TEXT,
        "image",
        "Python packages to install into the image, such as plugins.",
        default="",
        scope=Scope.BUILD,
    ),
    Variable(
        "POSTULO_EXTRA_APT_PACKAGES",
        Kind.TEXT,
        "image",
        "Debian packages to install into the image, such as fonts.",
        default="",
        scope=Scope.BUILD,
    ),
    Variable(
        "POSTULO_APT_REFRESH",
        Kind.TEXT,
        "image",
        "A time stamp that makes the build take Debian's packages afresh.",
        default="",
        scope=Scope.BUILD,
    ),
    # ------------------------------------------------------------------------- development
    Variable(
        "POSTULO_TEST_DATABASE_URL",
        Kind.URL,
        "development",
        "Runs the test suite against this database instead of SQLite in memory.",
        default="",
        scope=Scope.DEVELOPMENT,
        module="test",
        interpolate=False,
        empty_is_unset=True,
    ),
    Variable(
        "POSTULO_TEST_DB_CACHE",
        Kind.TEXT,
        "development",
        "Whether and where the test suite keeps a migrated database between runs.",
        default="",
        scope=Scope.DEVELOPMENT,
        interpolate=False,
    ),
    Variable(
        "POSTULO_WRITE_GOLDEN",
        Kind.BOOLEAN,
        "development",
        "Lets the calendar tests write their expected files again.",
        default=False,
        scope=Scope.DEVELOPMENT,
        interpolate=False,
    ),
    Variable(
        "POSTULO_SEED_PASSWORD",
        Kind.SECRET,
        "development",
        "The password `manage.py seed_demo` gives the demo account.",
        default="",
        scope=Scope.DEVELOPMENT,
        interpolate=False,
        empty_is_unset=True,
    ),
    Variable(
        "POSTULO_WIKI",
        Kind.PATH,
        "development",
        "The wiki checkout `scripts/screenshots.py` writes its pictures into.",
        default=REPO_DIR.parent / "postulo.wiki",
        scope=Scope.DEVELOPMENT,
        interpolate=False,
    ),
    Variable(
        "POSTULO_CATALOGUE_KEY",
        Kind.SECRET,
        "development",
        "The private key `scripts/official_catalogue.py sign` signs the official catalogue with.",
        default="",
        scope=Scope.DEVELOPMENT,
        interpolate=False,
        empty_is_unset=True,
    ),
)

#: Every variable, by name, in the order they are documented.
VARIABLES: dict[str, Variable] = {variable.name: variable for variable in _VARIABLES}


# ----------------------------------------------------------------------------------- reading

_MISSING = object()

_TRUE = ("true", "yes", "on", "1", "y", "ok")
_FALSE = ("false", "no", "off", "0", "n")

#: A size's suffixes, in 1024s, as the `_MB` names have always counted.
SIZE_UNITS = {
    "b": 1,
    "kb": 1024,
    "kib": 1024,
    "mb": 1024**2,
    "mib": 1024**2,
    "gb": 1024**3,
    "gib": 1024**3,
    "tb": 1024**4,
    "tib": 1024**4,
}
#: What a size named in a unit counts in.
NAMED_SIZE_UNITS = {"bytes": 1, "kilobytes": 1024, "megabytes": 1024**2}
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800}
NAMED_DURATION_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86_400}

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]+)\s*$", re.IGNORECASE)
_DURATION = re.compile(r"^\s*(\d+)\s*([a-z])\s*$", re.IGNORECASE)
_RATE = re.compile(r"^\s*(?:0?|\d+\s*/\s*[smhd])\s*$")


@dataclass(frozen=True)
class Problem:
    """A value `read` could not take as it was written."""

    name: str
    #: "error" where the value was misread or would have stopped the instance; else "warning".
    level: str
    message: str
    #: The syntax the variable expects.
    hint: str


@dataclass(frozen=True)
class Found:
    """Where a variable's value came from."""

    #: "environment", "file", "alias" or "default".
    source: str
    #: The variable it was read from: the name, its `_FILE`, or an old name.
    via: str
    raw: str | None
    unit: str = ""


@dataclass
class _Parsed:
    value: object
    problem: Problem | None = None
    #: Whether the declared default is used instead of the value.
    fallback: bool = False


def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _variable(name: str, variables: Mapping[str, Variable] | None) -> Variable:
    try:
        return (VARIABLES if variables is None else variables)[name]
    except KeyError:
        raise LookupError(f"{name} is not declared in postulo.core.configuration.") from None


def _interpolated(name: str, environ: Mapping[str, str]) -> str | None:
    """The value as django-environ reads it: `$OTHER` stands for OTHER's value."""
    import environ as django_environ

    reader = django_environ.Env()
    reader.ENVIRON = environ
    value = reader.get_value(name, cast=str, default=_MISSING)
    return None if value is _MISSING else value


def locate(variable: Variable, environ: Mapping[str, str] | None = None) -> Found:
    """Which variable gives this one its value: itself, its `_FILE`, an old name, or none.

    Refuses, at once, what cannot be resolved: a name set beside its old name or its file, and
    a file that cannot be read. The message names the variables and the path, never a value.
    """
    environ = _environ(environ)
    names = (variable.name, *(alias.name for alias in variable.aliases))
    given = [name for name in names if name in environ]
    file_name = variable.name + FILE_SUFFIX
    has_file = variable.kind is Kind.SECRET and file_name in environ
    if len(given) > 1:
        raise ImproperlyConfigured(
            f"{given[0]} and {given[1]} are both set. {given[1]} is the old name of "
            f"{variable.name}: remove it."
        )
    if given and has_file:
        raise ImproperlyConfigured(
            f"{given[0]} and {file_name} are both set; give the secret one way only."
        )
    if has_file:
        path = environ[file_name]
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            reason = getattr(exc, "strerror", None) or "it is not text"
            raise ImproperlyConfigured(
                f"{file_name} names {path}, which cannot be read: {reason}."
            ) from None
        return Found("file", file_name, text.removesuffix("\n").removesuffix("\r"))
    if not given:
        return Found("default", "", None)
    name = given[0]
    if name != variable.name:
        alias = next(a for a in variable.aliases if a.name == name)
        source, unit = "alias", alias.unit
    else:
        source, unit = "environment", ""
    raw = _interpolated(name, environ) if variable.interpolate else environ[name]
    if raw is None:
        return Found("default", "", None)
    return Found(source, name, raw, unit)


def _whole(amount: Decimal) -> int | None:
    return int(amount) if amount == amount.to_integral_value() else None


def syntax(variable: Variable) -> str:
    """The syntax a variable's value takes, in words."""
    kind = variable.kind
    if kind is Kind.BOOLEAN:
        if variable.accepts:
            return "1 to switch it on; nothing else is understood"
        return "true or false (also yes/no, on/off, 1/0)"
    if kind is Kind.INTEGER:
        return "a whole number" + _range_text(variable)
    if kind is Kind.SIZE:
        return (
            f"a whole number of {variable.unit}, or a size such as 25MB or 2GB "
            "(in 1024s)" + _range_text(variable)
        )
    if kind is Kind.DURATION:
        return (
            f"a whole number of {variable.unit}, or a duration such as 90s, 15m, 2h or 7d"
            + _range_text(variable)
        )
    if kind is Kind.RATE:
        return "<count>/<s|m|h|d>, such as 30/h; empty or 0 for no limit"
    if kind is Kind.URL:
        return "an address"
    if kind is Kind.LIST:
        return "values separated by commas"
    if kind is Kind.CHOICE:
        words = ", ".join(variable.choices)
        return f"one of: {words}" + (", or empty" if variable.blank else "")
    if kind is Kind.SECRET:
        return f"text, or {variable.name}{FILE_SUFFIX} naming a file that holds it"
    if kind is Kind.PATH:
        return "a path"
    return "text"


def _range_text(variable: Variable) -> str:
    low, high = variable.minimum, variable.maximum
    if low is not None and high is not None:
        return f", {low} to {high}"
    if low is not None:
        return f", {low} or more"
    return ""


def _problem(variable: Variable, found: Found, level: str, why: str) -> Problem:
    return Problem(
        variable.name,
        level,
        f"{found.via} is {found.raw!r}: {why}.",
        f"{found.via} takes {syntax(variable)}.",
    )


def _parse(variable: Variable, found: Found) -> _Parsed:
    raw = found.raw
    assert raw is not None
    kind = variable.kind
    if variable.default is None and raw == "":
        return _Parsed(None)
    if kind is Kind.BOOLEAN:
        return _parse_boolean(variable, found)
    if kind in (Kind.INTEGER, Kind.SIZE, Kind.DURATION):
        return _parse_number(variable, found)
    if kind is Kind.LIST:
        return _Parsed([item.strip() for item in raw.split(",") if item.strip()])
    if kind is Kind.CHOICE:
        word = raw.strip()
        if word == "" and variable.blank:
            return _Parsed("")
        for choice in variable.choices:
            if word.lower() == choice.lower():
                return _Parsed(choice)
        return _Parsed(
            word.lower(),
            _problem(variable, found, "error", "it is not one of the words it takes"),
        )
    if kind is Kind.RATE:
        if _RATE.match(raw):
            return _Parsed(raw)
        return _Parsed(raw, _problem(variable, found, "error", "it is not a rate"))
    return _Parsed(raw)


def _parse_boolean(variable: Variable, found: Found) -> _Parsed:
    raw = found.raw or ""
    if variable.accepts:
        if raw in variable.accepts or raw == "":
            return _Parsed(raw == "1")
        return _Parsed(
            False,
            _problem(variable, found, "error", "only 1 switches it on, so this is read as off"),
        )
    # django-environ's reading, kept: a whole number is its truth, and anything else is true
    # only if it is one of its words.
    try:
        value = int(raw) != 0
    except ValueError:
        word = raw.strip().lower()
        value = word in _TRUE
        if word in _TRUE or word in _FALSE:
            return _Parsed(value)
        if word == "":
            if variable.default is True:
                return _Parsed(
                    value,
                    _problem(
                        variable,
                        found,
                        "warning",
                        "empty is read as false; unset it to take the default, true",
                    ),
                )
            return _Parsed(value)
        return _Parsed(
            value, _problem(variable, found, "error", "it is not a boolean, so it is read as false")
        )
    return _Parsed(value)


def _parse_number(variable: Variable, found: Found) -> _Parsed:
    raw = found.raw or ""
    kind = variable.kind
    try:
        number = int(raw)
    except ValueError:
        number = None
    if number is not None and found.unit and found.unit != variable.unit:
        number = _convert(
            Decimal(number), _unit_size(kind, found.unit), _unit_size(kind, variable.unit)
        )
    if number is None and kind is not Kind.INTEGER:
        number = _suffixed(kind, raw, _unit_size(kind, variable.unit))
    if number is None:
        what = {
            Kind.INTEGER: "it is not a whole number",
            Kind.SIZE: f"it is not a whole number of {variable.unit} or a size",
            Kind.DURATION: f"it is not a whole number of {variable.unit} or a duration",
        }[kind]
        return _Parsed(
            variable.default,
            _problem(variable, found, "error", what + ", so the default is used"),
            fallback=True,
        )
    low, high = variable.minimum, variable.maximum
    if low is not None and number < low:
        return _Parsed(number, _problem(variable, found, "warning", f"it is less than {low}"))
    if high is not None and number > high:
        return _Parsed(number, _problem(variable, found, "warning", f"it is more than {high}"))
    return _Parsed(number)


def _unit_size(kind: Kind, unit: str) -> int:
    table = NAMED_SIZE_UNITS if kind is Kind.SIZE else NAMED_DURATION_UNITS
    return table[unit]


def _convert(amount: Decimal, source: int, target: int) -> int | None:
    return _whole(amount * source / target)


def _suffixed(kind: Kind, raw: str, target: int) -> int | None:
    if kind is Kind.SIZE:
        match = _SIZE.match(raw)
        if not match or match[2].lower() not in SIZE_UNITS:
            return None
        try:
            amount = Decimal(match[1])
        except InvalidOperation:
            return None
        return _convert(amount, SIZE_UNITS[match[2].lower()], target)
    match = _DURATION.match(raw)
    if not match or match[2].lower() not in DURATION_UNITS:
        return None
    return _convert(Decimal(match[1]), DURATION_UNITS[match[2].lower()], target)


def read(
    name: str,
    default: object = _MISSING,
    *,
    environ: Mapping[str, str] | None = None,
    variables: Mapping[str, Variable] | None = None,
):
    """The value of a declared variable, read from the environment, or its default.

    `default` is for a default that depends on another variable, as the mail encryption's does
    on the older switch; everything else takes the one declared. A value that cannot be read
    falls back as it did before the registry (see the module's docstring) and is reported by
    `problems`.
    """
    variable = _variable(name, variables)
    fallback = variable.default if default is _MISSING else default
    found = locate(variable, environ)
    if found.raw is None or (variable.empty_is_unset and found.raw == ""):
        return fallback
    parsed = _parse(variable, found)
    return fallback if parsed.fallback else parsed.value


def is_set(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    variables: Mapping[str, Variable] | None = None,
) -> bool:
    """Whether the environment gives this variable a value: by its name, its file or an old name."""
    variable = _variable(name, variables)
    environ = _environ(environ)
    names = [variable.name, *(alias.name for alias in variable.aliases)]
    if variable.kind is Kind.SECRET:
        names.append(variable.name + FILE_SUFFIX)
    return any(candidate in environ for candidate in names)


def source(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    variables: Mapping[str, Variable] | None = None,
) -> Found:
    """Where a variable's value comes from, for `manage.py settings`."""
    return locate(_variable(name, variables), environ)


# ---------------------------------------------------------------------------------- checking


def known_names(variables: Mapping[str, Variable] | None = None) -> set[str]:
    """Every name the environment may hold: each variable, its file and its old names."""
    known: set[str] = set()
    for variable in (VARIABLES if variables is None else variables).values():
        known.add(variable.name)
        known.update(alias.name for alias in variable.aliases)
        if variable.kind is Kind.SECRET:
            known.add(variable.name + FILE_SUFFIX)
    return known


def unknown(
    environ: Mapping[str, str] | None = None,
    variables: Mapping[str, Variable] | None = None,
) -> list[tuple[str, str | None]]:
    """Each `POSTULO_*` name set that nothing reads, with the nearest one that something does."""
    known = known_names(variables)
    candidates = sorted(name for name in known if name.startswith("POSTULO_"))
    found = []
    for name in sorted(_environ(environ)):
        if name.startswith("POSTULO_") and name not in known:
            nearest = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
            found.append((name, nearest[0] if nearest else None))
    return found


def deprecated(
    environ: Mapping[str, str] | None = None,
    variables: Mapping[str, Variable] | None = None,
) -> list[tuple[str, str]]:
    """Each old name set, with the name that replaces it."""
    environ = _environ(environ)
    return [
        (alias.name, variable.name)
        for variable in (VARIABLES if variables is None else variables).values()
        for alias in variable.aliases
        if alias.name in environ
    ]


def problems(
    environ: Mapping[str, str] | None = None,
    variables: Mapping[str, Variable] | None = None,
) -> list[Problem]:
    """Every value set that cannot be taken as written.

    A variable another start-up check already refuses (`checked_by`) is left to that check,
    so nothing is reported twice. A variable whose file cannot be read is reported here too,
    for `manage.py settings`, which reads without stopping.
    """
    found_problems = []
    for variable in (VARIABLES if variables is None else variables).values():
        try:
            found = locate(variable, environ)
        except ImproperlyConfigured as exc:
            found_problems.append(Problem(variable.name, "error", str(exc), syntax(variable)))
            continue
        if found.raw is None or (variable.empty_is_unset and found.raw == ""):
            continue
        parsed = _parse(variable, found)
        if parsed.problem and not variable.checked_by:
            found_problems.append(parsed.problem)
    return found_problems


# ------------------------------------------------------------------------------- describing


def display_default(variable: Variable) -> str:
    """The default, as a person reads it."""
    if variable.default_text:
        return variable.default_text
    return display(variable, variable.default)


def display(variable: Variable, value: object) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        try:
            return value.relative_to(REPO_DIR).as_posix()
        except ValueError:
            return value.as_posix()
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value) if value else "empty"
    if value == "":
        return "empty"
    text = str(value)
    if variable.kind is Kind.PATH:
        try:
            return Path(text).relative_to(REPO_DIR).as_posix()
        except ValueError:
            return text
    return text


def in_area(area: str, variables: Mapping[str, Variable] | None = None) -> list[Variable]:
    return [v for v in (VARIABLES if variables is None else variables).values() if v.area == area]
