# Installing Postulo

## The short version

```sh
git clone https://source.tiagoagueda.com/tiagoagueda/postulo.git
cd postulo
cp .env.example .env          # set POSTULO_SECRET_KEY and POSTULO_ALLOWED_HOSTS
docker compose -f docker/compose.yml up -d
docker compose -f docker/compose.yml exec postulo python manage.py createsuperuser
```

`createsuperuser` asks for a username, an email address, your first and last name, and a
password. That account's address counts as verified, so it can sign in before email
delivery is configured; everyone invited afterwards receives a verification link.

Then put a reverse proxy in front of port 8000 to terminate TLS. That is the whole
installation; the rest of this page is detail and the alternative without a container.

> **What has been tested.** The image has been built and run on a Raspberry Pi
> (arm64, Debian): it builds, migrates, passes its health check, serves pages, and
> renders a PDF with WeasyPrint. The Compose file above was followed exactly as written.
> What has *not* happened is somebody running it for months of a real job search, so
> treat it as working rather than as proven. `scripts/check-image.sh` repeats that whole
> check wherever you have Docker.

## What you need

- **Python 3.12, 3.13 or 3.14**
- **[uv](https://docs.astral.sh/uv/)** to install dependencies
- **git**
- **Pango**, if you want PDF export and are installing without a container. On Debian
  or Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`. The image already has
  it. Postulo works without it; you simply cannot export PDFs.

Node is **not** required. The stylesheet is compiled and committed; Node is only needed
if you want to change the CSS.

## With Docker

The image carries everything Postulo needs, including Pango, so PDF export works out of
the box.

**SQLite**, which is the right choice for a personal instance — one file to back up, and
a job search does not produce the kind of load that needs more:

```sh
docker compose -f docker/compose.yml up -d
```

**PostgreSQL**, if you already run one and would rather have a single backup regime.
Set `POSTGRES_PASSWORD` in `.env` first:

```sh
docker compose -f docker/compose.postgres.yml up -d
```

Both bind to `127.0.0.1:8000` rather than to every interface, on the assumption that a
reverse proxy sits in front. Migrations run automatically on start, so upgrading is
pulling a new image and restarting.

Your data lives in the `postulo-data` volume: the database (on SQLite) and every
uploaded and generated file. That is what to back up — see
[Backups and your data](Backups-and-your-data).

To check the image builds and runs before trusting it with anything:

```sh
./scripts/check-image.sh
```

It builds, starts a container, and waits for the health check to answer.

**Do not point your reverse proxy at the data volume.** Uploaded CVs are delivered only
through a view that has established who is asking, and serving the directory would
bypass that entirely.

## Trying it on your own machine

```sh
git clone https://source.tiagoagueda.com/tiagoagueda/postulo.git
cd postulo
uv sync
cp .env.example .env
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver
```

Then open <http://127.0.0.1:8000>.

A development secret key is generated on first run and kept in `data/.dev-secret-key`, so
your session survives a restart. `runserver` is Django's development server: convenient,
single-threaded, and not for anything reachable from the internet.

## Putting it on a server

1. **Clone and install**, as above, but without the development extras:

   ```sh
   uv sync --no-dev
   ```

2. **Write a `.env`.** At minimum:

   ```sh
   POSTULO_SECRET_KEY=<a long random string>
   POSTULO_DEBUG=false
   POSTULO_ALLOWED_HOSTS=postulo.example.org
   POSTULO_CSRF_TRUSTED_ORIGINS=https://postulo.example.org
   POSTULO_TIME_ZONE=Europe/Paris
   ```

   Generate a key with:

   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```

   Every setting is listed in [Configuration](Configuration).

3. **Prepare the database and static files**, using the production settings:

   ```sh
   export DJANGO_SETTINGS_MODULE=postulo.config.settings.prod
   uv run manage.py migrate
   uv run manage.py collectstatic --noinput
   uv run manage.py createsuperuser
   ```

4. **Check your configuration.** This is worth doing before you expose anything:

   ```sh
   uv run manage.py check --deploy
   ```

5. **Run it with a real server.** Postulo is a standard WSGI application at
   `postulo.config.wsgi:application`. For example, with gunicorn:

   ```sh
   uv run gunicorn postulo.config.wsgi:application --bind 127.0.0.1:8000 --workers 3
   ```

   (gunicorn is not a dependency of Postulo; install whichever server you prefer.)

6. **Put a reverse proxy in front of it** that terminates TLS and forwards to that port.
   Postulo serves its own static files through WhiteNoise, so the proxy only needs to
   pass requests through. It **must not** serve `MEDIA_ROOT` — see
   [Files and what you sent](Files-and-what-you-sent) for why.

There is no background worker to run. Nothing in Postulo currently queues work.

## PDF rendering

**WeasyPrint is the default renderer and is installed with Postulo.** It produces
smaller, more faithful documents than a browser does, and needs no browser to launch.

What it does need is Pango and its companion libraries, which are one package manager
command away on Linux:

```sh
sudo apt install libpango-1.0-0 libpangoft2-1.0-0     # Debian and Ubuntu
sudo dnf install pango                                 # Fedora
sudo apk add pango                                     # Alpine
```

Those libraries are a genuine nuisance on Windows, so a fallback exists — headless
Chromium, driven by Playwright:

```sh
uv sync --extra chromium
uv run playwright install chromium
```

Postulo uses whichever actually works, preferring WeasyPrint. "Actually works" means it
tries to import the renderer rather than merely checking that the package is present:
WeasyPrint installs perfectly happily on a machine with no Pango and only fails when
asked to render, so presence is not evidence of anything.

To pin the choice, set `POSTULO_PDF_BACKEND` to `weasyprint` or `chromium`. Export is
optional throughout: tracking applications and writing letters need no renderer at all.

## Upgrading

Back up first — see [Backups and your data](Backups-and-your-data). Postulo is young
enough that an upgrade is worth being able to undo.

With Docker:

```sh
git pull
docker compose -f docker/compose.yml up -d --build
```

Migrations run on start, so there is no separate step to remember.

Without:

```sh
git pull
uv sync
uv run manage.py migrate
uv run manage.py collectstatic --noinput
# restart your server
```
