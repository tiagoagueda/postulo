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
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=postulo.config.settings.prod \
    POSTULO_MEDIA_ROOT=/app/data/media \
    POSTULO_BACKUP_DIR=/app/data/backups \
    POSTULO_STATIC_ROOT=/app/staticfiles

# libpango and friends are WeasyPrint's; curl is for the health check. Nothing else.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 fonts-dejavu-core \
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

COPY manage.py ./
COPY --from=styles /build/src/postulo/static/css/app.css ./src/postulo/static/css/app.css

# Collected once, at build time: the running container should not need to write to its
# own image, and a read-only filesystem is a reasonable thing for an operator to want.
# The key here exists only for the length of this command and never reaches the image.
RUN POSTULO_SECRET_KEY=build-time-only-not-a-secret \
    POSTULO_ALLOWED_HOSTS=localhost \
    uv run manage.py collectstatic --noinput --clear

COPY docker/entrypoint.sh /usr/local/bin/postulo-entrypoint
RUN chmod +x /usr/local/bin/postulo-entrypoint \
    && useradd --system --create-home --uid 1000 postulo \
    && mkdir -p /app/data/media /app/data/backups \
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
