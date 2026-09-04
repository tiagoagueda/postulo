# Postulo

**Self-hosted job application manager, from the applicant's side of the table.**

Every applicant tracking system is built for the company doing the hiring. Postulo is
built for the person applying: your applications, your CVs, your cover letters, your
data, on your server.

From the Latin *postulō* — "I apply for". First person, deliberately.

> **Status: pre-alpha.** Under active construction. Not yet usable.

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
- **Capture postings** manually or from a URL, extensible through plugins.
- **See the funnel** — response rates, time to response, and which sources actually
  convert.

## Principles

- **Your data is yours.** Full JSON + media export, always one command away.
- **No telemetry.** No outbound calls except URL captures you explicitly trigger.
- **Private by default.** Uploaded documents are never publicly served.
- **Boring, durable stack.** Django, SQLite or PostgreSQL, server-rendered HTML.

## Documentation

- **[The wiki](https://source.tiagoagueda.com/tiagoagueda/postulo/wiki)** — installing,
  configuring and using Postulo. Authored in [wiki/](wiki/) and published from there.
- [Implementation plan](docs/PLAN.md) — architecture, data model, and milestones

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                      # install dependencies
cp .env.example .env         # configure (a dev SECRET_KEY is generated if unset)
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver
```

PDF export needs a renderer, which is optional — Postulo tracks applications and
writes letters perfectly well without one:

```sh
uv sync --extra weasyprint              # Linux and containers; needs GTK
uv sync --extra chromium                # anywhere
uv run playwright install chromium      # then fetch the browser
```

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
