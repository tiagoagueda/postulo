# Postulo

[![CI](https://source.tiagoagueda.com/tiagoagueda/postulo/actions/workflows/ci.yml/badge.svg)](https://source.tiagoagueda.com/tiagoagueda/postulo/actions)

**Self-hosted job application manager, from the applicant's side of the table.**

Every applicant tracking system is built for the company doing the hiring. Postulo is
built for the person applying: your applications, your CVs, your cover letters, your
data, on your server.

From the Latin *postulō* — "I apply for". First person, deliberately.

> **Status: 0.1.0.** Usable, and used. Not yet battle-tested: it has recorded real
> applications, but by one person, for days rather than months.

## What it does

- **Track applications** end to end — from a posting you spotted to an offer, with an
  append-only timeline that records what actually happened and when.
- **Manage CVs** as structured, reusable career content: write an experience once, then
  compose targeted CV variants from it without copy-pasting between documents.
- **Manage cover letters** from reusable templates with per-application placeholders.
- **Keep what you actually sent.** Every document is snapshotted to PDF at send time, so
  six months later you know exactly which version that employer read.
- **Bring your own files.** Externally authored PDFs and DOCX files are stored and
  versioned alongside generated ones.
- **Capture postings** from a URL — Postulo reads the page and asks you to confirm it
  before recording anything. Extensible through plugins, and reachable through a small
  API for scripts and browser extensions.
- **See what is working** — how far applications get, what share are answered, how long
  employers take, and which sources convert. Read from the timeline, so an interview that
  ended in a rejection still counts as an interview.
- **Take everything with you** — one zip holding a readable JSON document of every record
  and every file, which imports back.

## Principles

- **Your data is yours.** Full JSON + media export, always one command away.
- **No telemetry.** No outbound calls except URL captures you explicitly trigger.
- **Private by default.** Uploaded documents are never publicly served.
- **Boring, durable stack.** Django, SQLite or PostgreSQL, server-rendered HTML.

## Documentation

- **[The wiki](https://source.tiagoagueda.com/tiagoagueda/postulo/wiki)** — installing,
  configuring and using Postulo. Authored in [wiki/](wiki/) and published from there.
- [Implementation plan](docs/PLAN.md) — architecture, data model, and milestones
- [Writing a capture source](docs/PLUGINS.md) — the plugin contract

## Running it

```sh
git clone https://source.tiagoagueda.com/tiagoagueda/postulo.git
cd postulo
cp .env.example .env          # set POSTULO_SECRET_KEY and POSTULO_ALLOWED_HOSTS
docker compose -f docker/compose.yml up -d
docker compose -f docker/compose.yml exec postulo python manage.py createsuperuser
```

Then put a reverse proxy in front of it for TLS. See
[Installing Postulo](https://source.tiagoagueda.com/tiagoagueda/postulo/wiki/Installing-Postulo)
for the full instructions, including installing without a container.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                      # install dependencies
cp .env.example .env         # configure (a dev SECRET_KEY is generated if unset)
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver
```

PDF export uses **WeasyPrint**, which is installed with Postulo. On Linux it needs
Pango:

```sh
sudo apt install libpango-1.0-0 libpangoft2-1.0-0     # Debian and Ubuntu
```

Those libraries are awkward to obtain on Windows, so a fallback renderer exists there:

```sh
uv sync --extra chromium
uv run playwright install chromium
```

Postulo uses whichever works, preferring WeasyPrint. Export is optional: tracking
applications and writing letters need no renderer at all.

Node is **not** required to run Postulo: the compiled stylesheet is committed. It is
only needed to change the CSS, in which case:

```sh
npm install
npm run watch:css            # or `npm run build:css` for a one-off
```

Tests and linting:

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Licence

[AGPL-3.0-or-later](LICENSE). If you run a modified Postulo as a network service, your
users are entitled to its source.

## Where this lives

Developed on [Forgejo](https://source.tiagoagueda.com/tiagoagueda/postulo) and mirrored to
GitHub. Issues and pull requests belong on the Forgejo repository; the GitHub copy is a
read-only mirror.
