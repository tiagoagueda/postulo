# Postulo, as one image.
#
# Two stages. Node builds the stylesheet and is then thrown away, so the image that
# actually runs carries Python and nothing else — the compiled CSS is committed to the
# repository as well, but building it here means the image is right even if somebody
# forgot to rebuild before committing.
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
RUN npm run build:css


# -------------------------------------------------------------------- runtime
FROM python:3.14-slim-bookworm AS runtime

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
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 \
        fonts-dejavu-core fonts-noto-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a change to the application does not re-resolve them.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts/messages.py ./scripts/messages.py

# The translation catalogues are committed as .po text; Django reads the compiled .mo,
# which this writes in plain Python — no gettext in the image.
RUN python scripts/messages.py compile

RUN uv sync --locked --no-dev --extra server

# Plugins baked into the image. A plugin is an ordinary Python package installed into
# Postulo's environment; this argument takes any number of them, in whatever form pip
# accepts — a name from an index, a wheel URL, git+https://… — and installs them at
# build time so the image is complete and immutable. Git is needed only for the git
# form, and only for the length of this step; it is removed again so the runtime image
# stays as small as it was. The core's lock is a constraint, so a plugin can never
# change the version of a package Postulo itself pins.
ARG POSTULO_EXTRA_PACKAGES=""
RUN if [ -n "$POSTULO_EXTRA_PACKAGES" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends git \
        && uv export --locked --no-dev --extra server --no-hashes --no-emit-project \
            --output-file /tmp/constraints.txt \
        && uv pip install --no-cache --constraint /tmp/constraints.txt $POSTULO_EXTRA_PACKAGES \
        && rm -f /tmp/constraints.txt \
        && apt-get purge -y git && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY manage.py ./
COPY --from=styles /build/src/postulo/static/css/app.css ./src/postulo/static/css/app.css

# Collected once, at build time: the running container should not need to write to its
# own image, and a read-only filesystem is a reasonable thing for an operator to want.
# The key here exists only for the length of this command and never reaches the image.
# --no-sync: the environment above is the one to use; a plain `uv run` would otherwise
# resolve the development groups back into the image.
RUN POSTULO_SECRET_KEY=build-time-only-not-a-secret \
    POSTULO_ALLOWED_HOSTS=localhost \
    uv run --no-sync manage.py collectstatic --noinput --clear

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
