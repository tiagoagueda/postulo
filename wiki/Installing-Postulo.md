# Installing Postulo

> **There is no container image yet.** Docker and Compose files are planned for milestone
> M6 — see [Roadmap](Roadmap). Until then, installation is manual. It is not difficult,
> but it is not one command either.

## What you need

- **Python 3.12, 3.13 or 3.14**
- **[uv](https://docs.astral.sh/uv/)** to install dependencies
- **git**
- **Pango**, if you want PDF export. On Debian or Ubuntu:
  `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`. Postulo works without it; you
  simply cannot export PDFs. See below.

Node is **not** required. The stylesheet is compiled and committed; Node is only needed
if you want to change the CSS.

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

While Postulo is pre-alpha, treat every upgrade as one you might have to undo:

```sh
# Back up first — see Backups and your data
git pull
uv sync
uv run manage.py migrate
uv run manage.py collectstatic --noinput
# restart your server
```
