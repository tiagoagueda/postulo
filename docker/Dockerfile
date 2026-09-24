# Postulo, as one image.
#
# Three stages, and the runtime is the only one that ships. Node builds the stylesheet;
# Python builds the environment and compiles the catalogues; the runtime copies in what
# runs and nothing else.
#
# **The stage boundary is what makes the trimming real** (#157). While the Python side was
# single-stage, every intermediate was already in a layer and a later `RUN rm` freed
# nothing — 296 MB of uv's download cache and 19 MB of `.po` source catalogues shipped
# because they could not be deleted afterwards, only never written. Copying forward the
# `.venv`, the source and the collected static is the version of that which stays true as
# the build grows: what is not copied is not there, without anybody having to remember a
# flag.
#
# The stylesheet is committed to the repository as well, but building it here means the
# image is right even if somebody forgot to rebuild before committing.
#
# The runtime installs Pango, because WeasyPrint is the default PDF renderer and loads it
# through the system linker. That is the one place a container earns its keep over a
# manual install: on Debian it is one apt line, and on the machine most people develop on
# it is an afternoon.

# ----------------------------------------------------------------- stylesheet
FROM node:22-bookworm-slim AS styles

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Tailwind reads the templates to decide which classes to emit, so the source has to be
# here even though nothing in it is executed.
COPY assets ./assets
COPY src ./src
# Not minified, and the same command a contributor runs, so the image's copy and the
# committed one are byte for byte the same file. WhiteNoise compresses what it serves and
# minifying first saves 819 bytes of the 11 kB it sends -- which is not worth a stylesheet
# nobody can read a diff of (#159).
RUN npm run build:css


# --------------------------------------------------------------- python build
# Thrown away whole. Everything here — uv, the lock, the `.po` catalogues, whatever git
# the extra-packages step needed — exists only to produce two directories: the virtual
# environment, and the source with its catalogues compiled.
FROM python:3.14-slim-bookworm AS build

# Pinned to the patch, the same one ci.yml and .pre-commit-config.yaml name, so the tool
# that reads the lock is as fixed as the lock (#233). The base images above and below stay
# at a major, on purpose: they are where Debian's security updates arrive from.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so a change to the application does not re-resolve them.
COPY pyproject.toml uv.lock README.md ./

# `--no-default-groups`, not `--no-dev`. `--no-dev` omits the group called `dev` and
# nothing else, and `pyproject.toml` declares two — `dev` and `e2e` — with both in
# `default-groups` so that `uv sync` never quietly removes the browser test's
# dependencies. The line therefore read correctly and installed Playwright anyway: 136 MB
# of test tool, bundling its own Node.js runtime, in an image that runs neither (#154).
# Naming the groups to exclude would need editing every time one is added; excluding all
# of them and asking for the extra by name stays right on its own.
#
# `--no-cache`: uv unpacks every wheel into ~/.cache/uv and keeps it, 296 MB of it (#154).
# This
# stage no longer ships, so the cache would go away regardless — the flag stays because it
# also keeps the build's own disk use honest, and because the reasoning that put it here is
# the reasoning that says not to write what nothing reads.
#
# Twice, and `--no-install-project` only on the first. `uv sync` installs the project as well
# as its dependencies, and before `COPY src` there is no project to install: with one sync
# up here every build failed on "Expected a Python module at: src/postulo/__init__.py", from
# #157 until the test instance's deploy built an image for the first time since (#166). The
# second sync, once the source is here, installs Postulo itself and nothing else, so a change
# to the application still does not re-resolve a single dependency.
#
# `--extra postgres` beside `--extra server`, because `docker/compose.postgres.yml` offers
# this image a PostgreSQL to run against and the driver lives in that extra. Without it the
# entrypoint's `migrate` stopped on an unimportable backend the moment anybody took the
# offer up, which nobody in this repository ever had (#219). 3 MB of psycopg binary is a
# small price for the second engine the project documents actually working; the alternative
# — a separate tag for it — is two images to build, scan and publish.
RUN uv sync --locked --no-default-groups --extra server --extra postgres --no-cache --no-install-project

COPY src ./src
COPY scripts/messages.py ./scripts/messages.py

RUN uv sync --locked --no-default-groups --extra server --extra postgres --no-cache

# The translation catalogues are committed as .po text; Django reads the compiled .mo,
# which this writes in plain Python — no gettext in the image. Every set of them, since a
# plugin Postulo ships carries its own (#127).
#
# Then the .po files go: 476 files and 20 MB of source that nothing at run time reads, of
# which the six plugin catalogues are the newest part. `status.json` is not one of them —
# the language picker reads it to say how far along each translation is — so this deletes
# by extension rather than by directory.
RUN python scripts/messages.py compile \
    && find src -name '*.po' -delete

# Plugins baked into the image. A plugin is an ordinary Python package installed into
# Postulo's environment; this argument takes any number of them, in whatever form pip
# accepts — a name from an index, a wheel URL, git+https://… — and installs them at
# build time so the image is complete and immutable. Git is needed only for the git form,
# and this stage does not ship, so it is simply left behind rather than purged. The core's
# lock is a constraint, so a plugin can never change the version of a package Postulo
# itself pins.
#
# The export names the same extras as the syncs above. It is a constraint file, so anything
# it leaves out is simply unconstrained: with `postgres` missing, a plugin that happened to
# depend on psycopg could pull in a different version from the one the environment already
# had, which is exactly what a constraint file exists to prevent (#219).
ARG POSTULO_EXTRA_PACKAGES=""
RUN if [ -n "$POSTULO_EXTRA_PACKAGES" ]; then \
        apt-get update \
        && apt-get upgrade -y \
        && apt-get install -y --no-install-recommends git \
        && uv export --locked --no-dev --extra server --extra postgres --no-hashes \
            --no-emit-project --output-file /tmp/constraints.txt \
        && uv pip install --no-cache --constraint /tmp/constraints.txt $POSTULO_EXTRA_PACKAGES \
        && rm -rf /var/lib/apt/lists/*; \
    fi


# -------------------------------------------------------------------- runtime
FROM python:3.14-slim-bookworm AS runtime

# The same base as the build stage, which is what makes copying a virtual environment
# between them sound: `pyvenv.cfg` and every console script name an interpreter by
# absolute path, and here it is the same interpreter at the same path.
#
# uv is kept, and it is a decision rather than an oversight: 45 MB of the image is this
# binary, and `plugins/installing.py` prefers it "where the image put it", falling back to
# pip. Dropping it would not break installing a plugin through the interface; it would make
# it slower and hand an operator a different resolver than the one this project tests with.
# A working feature is worth more than 45 MB of a 400 MB image (#157).
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POSTULO_PLUGINS_DIR=/app/data/plugins \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=postulo.config.settings.prod \
    POSTULO_MEDIA_ROOT=/app/data/media \
    POSTULO_BACKUP_DIR=/app/data/backups \
    POSTULO_GEOLOCATIONS_DIR=/app/data/geonames \
    POSTULO_STATIC_ROOT=/app/staticfiles

# The city table the map places locations from is read from the data volume, not from
# the source layer it would otherwise sit in: the running container never writes to its
# own image, and a dataset written there would die with the layer an upgrade replaces.
# The entrypoint fetches it on first start-up, and a fetch that fails is a map without
# dots, not a refused start-up.

# libpango and friends are WeasyPrint's; curl is for the health check.
#
# fonts-noto-core is for the scripts. DejaVu draws Latin, Greek and Cyrillic and stops
# there, so an Amharic or Tigrinya CV rendered in this image came out a page of empty
# boxes, and a box on somebody's CV is worse than English. Noto's core set covers
# Arabic and Ethiopic (#70), and the Indic scripts and Hebrew that #71 will want. CJK
# is a separate package and a deliberate absence: the image does not double in size for
# a language almost none of the installations will use, and the door for it is
# POSTULO_EXTRA_APT_PACKAGES below (#74).
# tests/test_fonts.py holds this list to the languages Postulo actually offers.
#
# What the declaration does not say — whether the machine actually has the fonts —
# `manage.py check_fonts` asks of the running instance, and *Server settings →
# Overview* shows the answer (#74).
#
# `apt-get upgrade` before the install, and it is a decision rather than hygiene (#155).
# Debian publishes a security update; the base image keeps what it was built with until
# it is rebuilt; and a package Postulo never asked for -- libpcre2, arriving with Python
# -- then carries a fixable High for as long as nobody looks. That is what happened.
#
# It costs reproducibility: two builds of this commit a week apart are not the same
# image. The alternative is pinning the base by digest, which buys that back and makes
# Debian's updates arrive only when somebody remembers to bump the digest -- and a manual
# step nobody performs is precisely how the finding got here. Between an image that
# drifts towards being patched and one that reliably stays unpatched, this takes the
# first. A release is published by digest, so the artefact anybody runs is still named
# exactly.
#
# `upgrade`, not `dist-upgrade`: within a stable release the security updates are there,
# and nothing should be installing new packages or removing existing ones behind the
# build. It belongs in this RUN, after `update` and before `install`: a layer of its own
# would either run against a stale index or be served from cache while the install was
# not. tests/test_image_build.py holds it in that order.
#
# `libharfbuzz-subset0` because WeasyPrint 70 warns at import without it and says a later
# version will require it (#163).
#
# `postgresql-client-17` is `manage.py backup` and `manage.py restore` on the engine
# `docker/compose.postgres.yml` offers: those commands shell out to pg_dump and pg_restore
# rather than copying a file that is being written to, and stopped with "pg_dump is not on
# the PATH" in every image published so far (#219). **From PostgreSQL's own repository, and
# by major version**, which is the whole of why this is six lines instead of one word:
# Debian bookworm ships client 15, pg_dump refuses a server newer than itself, and
# `postgres:17` is what the compose file starts — so plain `postgresql-client` would have
# installed a tool that fails on the only server it would ever be pointed at. The armoured
# key goes in beside the source and `signed-by` names it, so apt trusts that repository for
# nothing else and no keyring tooling is needed. tests/test_image_build.py reads the major
# out of the compose file, so the two cannot drift apart.
#
# One layer, with the second index refresh inside it: the new repository has to be read
# before its package can be installed, and a `RUN` of its own would be served from cache
# exactly when the source line above it had changed.
# The door for packages the default image does not carry (#74). It takes any number of
# apt packages, in whatever form apt accepts, and installs them into this stage only.
# The build stage's POSTULO_EXTRA_PACKAGES is the pip door, and the two are separate
# questions: a Python package a plugin needs is not a font a document needs. The one
# decision it exists to hold is written above — CJK is opt-in, and this is how it is
# opted in:
#     docker build --build-arg POSTULO_EXTRA_APT_PACKAGES=fonts-noto-cjk ...
ARG POSTULO_EXTRA_APT_PACKAGES=""
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1 \
        fonts-dejavu-core fonts-noto-core \
        ca-certificates curl \
    && if [ -n "$POSTULO_EXTRA_APT_PACKAGES" ]; then \
           apt-get install -y --no-install-recommends $POSTULO_EXTRA_APT_PACKAGES; \
       fi \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# What runs, and nothing that built it: the environment, the source with its compiled
# catalogues, and the entry point. No lock, no uv cache, no `.po`, no build scripts.
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/src ./src
COPY manage.py ./
COPY --from=styles /build/src/postulo/static/css/app.css ./src/postulo/static/css/app.css

# Collected once, at build time: the running container should not need to write to its
# own image, and a read-only filesystem is a reasonable thing for an operator to want.
# --no-sync: the environment above is the one to use; a plain `uv run` would otherwise
# resolve the development groups back into the image.
#
# The key is generated here and goes away with the shell that made it. collectstatic needs
# one only because the settings module insists on one before it will import: nothing it
# writes is signed, and no session, cookie or stored credential exists at build time. It
# was a literal -- an honest name, and 28 characters -- until #111 taught the production
# settings to refuse anything under 50, and the image stopped building (#121). A longer
# literal would have satisfied the rule while still being a published constant somebody
# could paste into a .env. A random one satisfies it for the reason the rule exists.
#
# Plain `python`, which PATH resolves to the copied environment. It used to be `uv run
# --no-sync`, which needed the lock and the manifest present in this stage purely so that
# uv would agree to run a command; nothing else here wants them (#157).
#
# This collects Django admin's 6.6 MB whether or not an operator sets POSTULO_ADMIN_URL,
# because the admin is installed and its URL is read at run time. Making it a build
# argument would hand somebody an image whose admin page is missing its stylesheet the day
# they decide to enable it, which is a worse thing to own than 6.6 MB.
RUN POSTULO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
    POSTULO_ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput --clear

# The interpreter's own pip goes, and it is a security decision rather than slimming
# (#190). pip vendors a copy of msgpack for its HTTP cache, and that copy -- 1.1.2, from
# the base image -- was the **only fixable High** the first container scan could find. It
# is not in `uv.lock`, not in the virtual environment and not importable by Postulo, so no
# dependency bump reaches it; the package simply arrives with `python:3.14-slim-bookworm`
# and carries whatever its vendored tree carries.
#
# Nothing here needs it. `plugins/installing.py` prefers `uv`, which this image copies in
# deliberately a few lines above, and uv is a standalone binary that installs a plugin
# without pip present. The pip branch of `installer()` stays, because it is the right
# answer for an ordinary `pip install postulo` on somebody's server -- it is simply never
# taken in this image. `installer()` now says so plainly if it ever finds neither.
# Removed rather than uninstalled, and checked with the **base** interpreter by absolute
# path. `python` here is `/app/.venv/bin/python` -- PATH puts the environment first -- and
# that one never had pip, so `python -m pip uninstall` failed with *No module named pip*
# before removing anything. Which says something worth keeping: `installer()`'s pip branch
# could not have worked in this image even before pip was taken out of the interpreter,
# because `sys.executable` is the environment's python and pip was never in there.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
    && if /usr/local/bin/python -c "import pip" 2>/dev/null; then \
           echo "pip is still importable by the base interpreter" >&2; \
           exit 1; \
       fi \
    && uv --version

COPY docker/entrypoint.sh /usr/local/bin/postulo-entrypoint
RUN chmod +x /usr/local/bin/postulo-entrypoint \
    && useradd --system --create-home --uid 1000 postulo \
    && mkdir -p /app/data/media /app/data/backups /app/data/plugins \
    && chown -R postulo:postulo /app/data

# Nothing here needs root, and the data directory is the only thing it writes to.
USER postulo

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# How gunicorn itself is tuned, and the one lever an operator has over it (#220).
#
# `--timeout 120` rather than gunicorn's default of 30. Thirty seconds is a generous budget
# for a page and not one for drawing a PDF: a CV rendered by Chromium on a Raspberry Pi can
# take longer, and the worker was killed part way through with no answer and nothing in the
# log but a silent restart. Two minutes is long enough for the slow things Postulo genuinely
# does and short enough that a worker stuck on a network read is still recycled.
#
# `--max-requests 500 --max-requests-jitter 50` retires each worker after roughly five
# hundred requests. Not because anything is known to leak, but because a long-lived Python
# process serving one person's instance for months has no reason to be long-lived, and the
# jitter keeps the three from retiring together and leaving nobody to answer.
#
# Set here rather than in the CMD below so that an operator has one lever: setting
# GUNICORN_CMD_ARGS in the environment -- `environment:` in compose, `-e` on `docker run` --
# replaces this whole string, and the flags in the CMD are not repeated in it, so nothing
# argues with anything. gunicorn reads it as if it had been typed on the command line.
ENV GUNICORN_CMD_ARGS="--timeout 120 --max-requests 500 --max-requests-jitter 50"

ENTRYPOINT ["postulo-entrypoint"]
# The access line is gunicorn's default with one field added at the end: the X-Request-ID
# the response carries, which is the id every Postulo log line for that request carries too
# (#233). Laid side by side, the two logs now say which line belongs to which.
CMD ["gunicorn", "postulo.config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--access-logformat", "%(h)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\" request=%({x-request-id}o)s", \
     "--error-logfile", "-"]
