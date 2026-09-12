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

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

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
RUN uv sync --locked --no-default-groups --extra server --no-cache --no-install-project

COPY src ./src
COPY scripts/messages.py ./scripts/messages.py

RUN uv sync --locked --no-default-groups --extra server --no-cache

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
ARG POSTULO_EXTRA_PACKAGES=""
RUN if [ -n "$POSTULO_EXTRA_PACKAGES" ]; then \
        apt-get update \
        && apt-get upgrade -y \
        && apt-get install -y --no-install-recommends git \
        && uv export --locked --no-dev --extra server --no-hashes --no-emit-project \
            --output-file /tmp/constraints.txt \
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
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

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
    POSTULO_STATIC_ROOT=/app/staticfiles

# libpango and friends are WeasyPrint's; curl is for the health check.
#
# fonts-noto-core is for the scripts. DejaVu draws Latin, Greek and Cyrillic and stops
# there, so an Amharic or Tigrinya CV rendered in this image came out a page of empty
# boxes, and a box on somebody's CV is worse than English. Noto's core set covers
# Arabic and Ethiopic (#70), and the Indic scripts and Hebrew that #71 will want; CJK
# is a separate package and a separate decision when that phase arrives.
# tests/test_fonts.py holds this list to the languages Postulo actually offers.
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
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1 \
        fonts-dejavu-core fonts-noto-core \
        curl \
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
RUN python -m pip uninstall -y pip     && rm -rf /usr/local/lib/python3.14/site-packages/pip               /usr/local/lib/python3.14/site-packages/pip-*.dist-info     && python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pip') is not None)"     && uv --version

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

ENTRYPOINT ["postulo-entrypoint"]
CMD ["gunicorn", "postulo.config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
