<!-- Generated from ../README.md by scripts/github_readme.py; do not edit by hand. -->
> **This is a read-only mirror.** Postulo is developed at [source.tiagoagueda.com/postulo/postulo](https://source.tiagoagueda.com/postulo/postulo). Please open issues, suggestions and pull requests there.

<img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/brand/logo-256.png" alt="" width="96" align="left" hspace="16">

# Postulo

[![CI](https://source.tiagoagueda.com/postulo/postulo/actions/workflows/ci.yml/badge.svg)](https://source.tiagoagueda.com/postulo/postulo/actions)
[![Latest release](https://img.shields.io/gitea/v/release/postulo/postulo?gitea_url=https://source.tiagoagueda.com&label=release)](https://source.tiagoagueda.com/postulo/postulo/releases)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/LICENSE)
[![Python 3.12 or newer](https://img.shields.io/badge/python-3.12%2B-blue)](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/pyproject.toml)
[![Django 6.1](https://img.shields.io/badge/django-6.1-0c4b33)](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/pyproject.toml)
[![Docker image](https://img.shields.io/badge/docker-image-2496ed)](https://source.tiagoagueda.com/postulo/postulo/packages)
[![Translation progress](https://translate.tiagoagueda.com/widget/postulo/svg-badge.svg)](https://translate.tiagoagueda.com/projects/postulo/)
[![Last commit](https://img.shields.io/gitea/last-commit/postulo/postulo?gitea_url=https://source.tiagoagueda.com)](https://source.tiagoagueda.com/postulo/postulo/commits/branch/main)
[![No telemetry](https://img.shields.io/badge/telemetry-none-brightgreen)](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/SECURITY.md)
[![No paywall](https://img.shields.io/badge/paywall-never-brightgreen)](https://source.tiagoagueda.com/postulo/postulo#never-paywalled)

**Self-hosted job application manager, from the applicant's side of the table.**

Every applicant tracking system is built for the company doing the hiring. Postulo is
built for the person applying: your applications, your CVs, your cover letters, your
data, on your server.

From the Latin *postulō* — "I apply for". First person, deliberately.

> **Status: pre-1.0.** Usable and used, but not battle-tested: it has recorded real
> applications, by one person, for weeks rather than years. Releasing here is a deliberate
> act rather than something that happens on a schedule, so `main` may be ahead of the last
> tag — the [changelog](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/CHANGELOG.md) says by how much.

![The dashboard: what is due today, the applications still alive and the latest events across the whole search.](https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/assets/screenshots/dashboard.png)

| | |
| --- | --- |
| ![The board: one column per status from Draft to Offer, each application a card that can be dragged to the next column.](https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/assets/screenshots/board.png) | ![One application's page: its status, the company and contacts, and the timeline of everything that happened, newest first.](https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/assets/screenshots/application.png) |
| ![The calendar for the month: interviews and reminders on the days they fall.](https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/assets/screenshots/calendar.png) | ![A CV's page: the entries it is made of on one side and a live preview of the page as it will print on the other.](https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/assets/screenshots/cv.png) |

*These pictures are of a demo instance filled with a fictional job search, and are retaken by
`scripts/screenshots.py` rather than by hand. There is no public demo to try: it would be
something to host, reset and secure, and nobody's data belongs on it. Run your own with
`manage.py seed_demo <email>`; the [wiki](https://source.tiagoagueda.com/postulo/postulo/wiki)
shows every page.*

## Never paywalled

**No feature of Postulo is, or ever will be, behind a paywall.** People looking for work
are, more often than not, people who cannot afford to pay for the tools to find it.
Everything this software does is available in full to everyone who runs it: no paid
tier, no "pro" edition, no licence key, no feature that unlocks later. The licence
guarantees that for the code; this paragraph guarantees it for the project's intentions.

### If it is worth something to you

**Voluntary support is this project's only income.** Not its main one — its only one.
There is no company behind Postulo, no funding round, no paid tier waiting to appear once
enough people depend on it. One person writes it, and what it costs to keep going comes
from people who decided it was worth something.

<a href="https://www.buymeacoffee.com/tiagoagueda"><img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/assets/support/buy-me-a-coffee-qr.png" alt="Buy me a coffee — scan to open buymeacoffee.com/tiagoagueda" width="165" height="165"></a>

Nothing is owed. Nothing is unlocked by it, now or later — that is what the paragraph above
promises and it is not negotiable. And **nothing a person is shown while using Postulo to
look for work will ever ask for money**: no banner in the interface, no prompt, no reminder
on the dashboard. The one place inside the application that mentions support at all is
*Server settings → Overview*, the page addressed to whoever runs the instance, where it is
said once and in these words. Otherwise this is where the question is put, which is why it
is put plainly here.

If money is the wrong thing to give — and for a lot of people looking for work, it is —
then a bug report, a translation reviewed by somebody who actually speaks the language, or
telling one person who needs this that it exists are all worth as much.

[FUNDING.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/FUNDING.md) is the long version: what support pays for, what it will never
buy, and why money does not move an issue up the list.

## Secure, because of what it holds

**Postulo holds the most personal documents a person has while looking for work, and it
is built as if that were the only thing that mattered.** A CV carries a home address, a
phone number and a full employment history; a timeline says who has and has not replied.
So the code and the data are kept as secure as the project knows how to make them: every
record is owned and every query is scoped -- No one sees another person's data unless that person granted it -- explicitly, for a stated scope, revocably, and visibly to them at any time. Superusers can see and change any company, contact or listing, including private ones, but never anyone's notes, applications or documents. Files are never served without a permission
check, the browser is held to a strict content security policy, nothing is fetched from
the network without a deliberate decision, and the test suite includes security tests —
ownership sweeps, policy checks, the things an attacker would try — that fail the build.
Dependencies are checked for known vulnerabilities on every run and on a schedule, so a
fresh disclosure is noticed without anyone having to remember to look. A security report
is handled before any feature is; see [SECURITY.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/SECURITY.md).

## Built for everyone

**Postulo is meant to be usable by everyone, at its fullest, including people with
disabilities.** Looking for work is hard enough without the tool getting in the way. So
the interface is server-rendered HTML that works with scripts off, every control can be
reached and operated from the keyboard, images and icons carry names or are marked as
decorative, colour never carries a meaning on its own, changes on the page are announced
to screen readers, and the pages are checked against the accessibility guidelines
(WCAG 2.2, level AA) as part of the browser tests rather than as an afterthought. When a
feature cannot be made to work for someone, that is a bug, and it is filed as one.
[ACCESSIBILITY.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/ACCESSIBILITY.md) is the accessibility statement: what is checked and
how, what is known not to work yet, and how to report a barrier.

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
- **No telemetry.** Outbound requests only for the features listed in the wiki's *Hardening* page.
- **Private by default.** Uploaded documents are never publicly served.
- **Secure by obligation.** Ownership-scoped queries, permission-checked files, a strict
  content security policy, security tests in the suite, and dependency vulnerability
  checks in CI. See [SECURITY.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/SECURITY.md).
- **Accessible to everyone.** Keyboard-operable, screen-reader-announced, usable with
  scripts off, and checked against WCAG 2.2 AA in the browser tests.
- **Boring, durable stack.** Django, SQLite or PostgreSQL, server-rendered HTML.
- **Modular by design.** Anything that could reasonably vary — where a posting is read
  from, how you are told about things, how a PDF is produced — sits behind an interface
  that a separately installed package can implement. Postulo's own implementations are
  plugins that happen to ship in the box. See [Writing a plugin](https://source.tiagoagueda.com/postulo/postulo/wiki/Writing-a-plugin).

## Written with AI, answered for by people

Much of Postulo is written with an AI assistant, and contributions made the same way are
welcome. The rule is simple: a person proposes the change, has read it, can explain it,
and answers for it. The assistant is a tool; the contributor is accountable. See
[CONTRIBUTING.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/CONTRIBUTING.md) and [AGENTS.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/AGENTS.md).

## In your language

The interface is a setting per person; the documentation is in English, and the table
below says what Postulo is in each language so nobody has to guess. Translations are made,
corrected and reviewed at [translate.tiagoagueda.com](https://translate.tiagoagueda.com/projects/postulo/)
([docs/TRANSLATING.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/docs/TRANSLATING.md)), where the live progress of every language is.

<!-- languages:start (written by scripts/readme_languages.py; do not edit by hand) -->

Postulo speaks **10 languages**, each named here as its speakers write it.

| | Language | What Postulo is |
| --- | --- | --- |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/id.svg" width="20" height="15" alt=""> | **<span lang="id" dir="ltr">Bahasa Indonesia</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/ca.svg" width="20" height="15" alt=""> | **<span lang="en-CA" dir="ltr">English (Canada)</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/gb.svg" width="20" height="15" alt=""> | **<span lang="en-GB" dir="ltr">English (United Kingdom)</span>** | <span lang="en-GB" dir="ltr">Postulo is a self-hosted job application manager: your applications, CVs and cover letters on your own server, with no paid features.</span> |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/mx.svg" width="20" height="15" alt=""> | **<span lang="es-MX" dir="ltr">español (México)</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/ca.svg" width="20" height="15" alt=""> | **<span lang="fr-CA" dir="ltr">français (Canada)</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/fr.svg" width="20" height="15" alt=""> | **<span lang="fr-FR" dir="ltr">français (France)</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/es-ga.svg" width="20" height="15" alt=""> | **<span lang="gl" dir="ltr">galego</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/lt.svg" width="20" height="15" alt=""> | **<span lang="lt" dir="ltr">lietuvių</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/hu.svg" width="20" height="15" alt=""> | **<span lang="hu" dir="ltr">magyar</span>** | — |
| <img src="https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/src/postulo/static/flags/pt.svg" width="20" height="15" alt=""> | **<span lang="pt-PT" dir="ltr">português (Portugal)</span>** | — |

<!-- languages:end -->

## Documentation

- **[The wiki](https://source.tiagoagueda.com/postulo/postulo/wiki)** — installing,
  configuring and using Postulo. Its pages are their own repository,
  [postulo.wiki](https://source.tiagoagueda.com/postulo/postulo.wiki).
- [Implementation plan](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/docs/PLAN.md) — architecture, data model, and milestones
- [Writing a plugin](https://source.tiagoagueda.com/postulo/postulo/wiki/Writing-a-plugin) — the plugin contract, and every name a plugin may import

## Running it

```sh
git clone https://source.tiagoagueda.com/postulo/postulo.git
cd postulo
git checkout vX.Y.Z           # the latest release tag, see the releases page
cp .env.example .env          # set POSTULO_SECRET_KEY and POSTULO_ALLOWED_HOSTS
docker compose -f docker/compose.yml up -d
docker compose -f docker/compose.yml exec postulo python manage.py createsuperuser
```

That pulls the published image. To build it from the checkout instead, add the build
override: `docker compose -f docker/compose.yml -f docker/compose.build.yml up -d --build`.

Then put a reverse proxy in front of it for TLS. See
[Installing Postulo](https://source.tiagoagueda.com/postulo/postulo/wiki/Installing-Postulo)
for the full instructions, including installing without a container.

## Development

Requires Python 3.12.4 or newer and [uv](https://docs.astral.sh/uv/).

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
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0     # Debian and Ubuntu
```

Those libraries are awkward to obtain on Windows, so a fallback renderer exists there:

```sh
uv sync --extra chromium
uv run playwright install chromium
```

Postulo uses whichever works, preferring WeasyPrint. Export is optional: tracking
applications and writing letters need no renderer at all.

Node is **not** required to run Postulo: the compiled stylesheet is committed. It is
only needed to change the CSS, or to add a Tailwind class a template did not use before,
in which case:

```sh
npm install
npm run watch:css            # or `npm run build:css` for a one-off
```

To see it with data rather than an empty screen, fill an account with a fictional
search — thirty applications over six months, with documents:

```sh
uv run manage.py seed_demo you@example.org
```

Tests and linting:

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Licence

Copyright (C) 2026 Postulo contributors. [AGPL-3.0-or-later](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/LICENSE). If you run a modified
Postulo as a network service, your users are entitled to its source.

Some of what ships in the repository is somebody else's work under a licence of its own:
the [Lucide](https://lucide.dev) icons, the [flag-icons](https://github.com/lipis/flag-icons)
country flags, Tailwind CSS and basecoat-css compiled into the stylesheet, htmx and zxcvbn
served as scripts. All of it is copied into the repository, so nothing is fetched from
anybody else's server at runtime, and each carries its notice beside it.
[THIRD-PARTY.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/THIRD-PARTY.md) is the register.

The **name and the logo** are not covered by that licence. [TRADEMARKS.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/TRADEMARKS.md)
says what you may do without asking — which is nearly everything, including naming a plugin
`postulo-something` and calling your instance whatever you like — and the one case where a
fork should use another name. It also names the marks belonging to other people that appear
here, and none of their owners is involved with this project.

## Where this lives

Developed on [Forgejo](https://source.tiagoagueda.com/postulo/postulo) and mirrored to
GitHub. Issues and pull requests belong on the Forgejo repository; the GitHub copy is a
read-only mirror. A bug or a request has a form there (see
[CONTRIBUTING.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/CONTRIBUTING.md#opening-an-issue)); a security problem does not go in an
issue, see [SECURITY.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/SECURITY.md). Everyone taking part is held to the
[code of conduct](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/CODE_OF_CONDUCT.md).
