# Postulo — implementation plan

> **Status:** M0 to M3 complete. This is a living document, revised as milestones
> land and assumptions meet reality.

## 1. Mission

Every applicant tracking system on the market is built for the company doing the hiring.
Postulo is built for the person applying.

It is a self-hosted web application where a job seeker keeps their applications, their
CVs, their cover letters, and the record of what actually happened — on hardware they
control, in a database they can export, under a licence that keeps it open.

From the Latin *postulo*, "I apply for". First person, deliberately.

## 2. Product decisions

These were settled before any code was written, because each one is expensive to reverse.

| Decision | Choice | Consequence |
| --- | --- | --- |
| Tenancy | **Multi-user from day one** | Every user-owned model carries an owner; every query is scoped. No painful retrofit later. |
| Documents | **Hybrid** | Structured CV content is the source of truth *and* externally authored files can be uploaded and versioned. |
| AI assistance | **Not in v1** | The application is complete and useful without an API key. A plugin can add it later against the same contracts. |
| Posting capture | **Manual and URL, plugin-extensible** | A plugin interface and a capture API exist from M4, so a browser extension is a later addition rather than a rewrite. |
| Languages | **en-GB source; fr-FR and pt-PT via contributors** | Every user-facing string is translatable from the first commit. |
| Licence | **AGPL-3.0-or-later** | A modified Postulo run as a service must offer its source. |
| Hosting | **Forgejo primary, GitHub mirror** | CI lives in `.forgejo/`, which GitHub ignores. Issues and pull requests belong on Forgejo. |

## 3. Stack

Every version below was verified against PyPI and installed successfully on Python
3.14.7 during M0.

| Layer | Choice | Rationale |
| --- | --- | --- |
| Runtime | Python 3.12 to 3.14, managed by `uv` | 3.14 pinned locally via `.python-version`; CI covers all three |
| Framework | **Django 6.1** | 5.2 does not support Python 3.14. Brings built-in CSP, template partials, the Tasks API and `MAILERS` |
| Database | SQLite by default, PostgreSQL via `POSTULO_DATABASE_URL` | Self-hosting sanity: one file to back up. PostgreSQL for those who want it |
| Interface | Django templates, htmx 2, Tailwind v4 | Server-rendered. No SPA, and no API-first tax on an application of this scale. Alpine.js was dropped — see below |
| Authentication | django-allauth, email as identifier | Invite-only by default via `POSTULO_REGISTRATION_OPEN` |
| API | **django-ninja** | Pydantic schemas serve double duty as the plugin data contract. Verified working on Python 3.14 with Django 6.1 during M0 |
| PDF | **WeasyPrint** by default, installed with Postulo; Playwright Chromium as a fallback | WeasyPrint needs Pango, which is trivial on Linux and awkward on Windows. `POSTULO_PDF_BACKEND` overrides the auto-detection |
| Background work | Django's `django.tasks` API with **`django-tasks-db`** | Django 6.1 ships the API but no worker; this package supplies the ORM-backed backend and the `db_worker` process. No Redis, no Celery |
| Static files | WhiteNoise | Media is deliberately not served this way — see section 6 |
| Quality | ruff, pytest, pytest-django, factory-boy, coverage, pre-commit | `filterwarnings = error` stops deprecations from accumulating |

### Corrections made during M0

Two items in the original plan did not survive contact with the packages:

- **`django-tasks` was the wrong dependency.** As of 0.12.0 it is a pure backport of
  Django's Tasks API, which Django 6.1 already ships, and its database backend was split
  out into `django-tasks-db`. That is what Postulo depends on; the backport is
  unnecessary here.
- **`EMAIL_BACKEND` is deprecated in Django 6.1** in favour of `MAILERS`, and the two may
  not be combined. All settings use `MAILERS`. This was caught by treating warnings as
  errors, which is precisely why that setting is switched on.

### Corrections made during M1

- **Alpine.js was dropped.** Alpine evaluates its expressions with `new Function()`,
  which requires `script-src 'unsafe-eval'`. Weakening the Content-Security-Policy of an
  application that stores personal documents, in exchange for sprinkles of client-side
  state, is a poor trade. htmx needs no `eval`, and the small amount of behaviour left
  over is plain JavaScript.
- **The compiled stylesheet is committed.** Tailwind needs Node, and requiring it to run
  Postulo would be an unpleasant surprise for someone self-hosting a Python application.
  Node is needed only to *change* the CSS; CI rebuilds it and fails if the committed
  file has drifted.
- **allauth needs telling that the user model has no username**, via
  `ACCOUNT_USER_MODEL_USERNAME_FIELD = None`. Without it, its forms look for a column
  that does not exist.

### Corrections made during M2

- **The separate `Note` model was dropped.** It would have duplicated the event log,
  which already stores a timestamped entry with a body. A note is simply an event of
  kind "note", and companies and contacts keep a plain notes field. One timeline is
  easier to read, to query and to explain than two overlapping records.
- **Status transitions go through a service function**, never a signal. A signal fires
  on fixtures, imports and admin edits, where an automatic timeline entry is usually
  wrong. Routing the edit form through the same function was necessary too: saving the
  field directly left a status the log could not account for.
- **Aggregation drops `Meta.ordering`.** The paginated company list needed an explicit
  `order_by`, or pagination could repeat and skip rows between pages.
- **Number grouping is left to Django's locale machinery.** Hard-coding a thousands
  separator in a project that ships French and Portuguese would have been wrong in two
  of its three languages.

### Corrections made during M3

- **Highlights are text, one bullet per line, not rows.** Separate rows would have
  bought per-bullet reordering at the price of a formset on every editing screen, and
  would have turned a per-variant override into a fiddly set of selections rather than a
  textarea. The plan called for ordered bullet children; this is simpler and does the
  same job.
- **Cover letters are never rendered by Django's template engine.** A letter is text a
  person wrote, usually with fragments pasted from a job advert. Handing that to the
  template engine would let `{% ... %}` in the source reach into the application, so
  substitution is a regular expression over a fixed set of names and can do nothing
  else. There is a test that proves an expression in a letter is not evaluated.
- **Themes are choices, not user-editable rows.** A theme is a Django template plus a
  stylesheet; letting people upload those would mean executing their markup while
  rendering. User themes belong behind a deliberate decision.
- **A generic relation carries a real cost.** Two different models can share a primary
  key, so filtering `CVItem` by `object_id` alone matches rows it should not. Every
  query pairs it with the content type. A test caught this the hard way.
- **"Installed" and "usable" are different questions.** WeasyPrint is a Python package
  that loads Pango through the system linker, so on a machine without those libraries it
  is installed, findable, and raises OSError on import. Availability is therefore decided
  by attempting the import, not by asking whether the package exists.

## 4. Repository layout

```
postulo/
├── manage.py
├── pyproject.toml            # uv-managed; extras: postgres, weasyprint, chromium
├── docker/                   # Dockerfile, compose files, entrypoint            (M6)
├── docs/                     # PLAN.md, TRANSLATING.md, INSTALL.md, PLUGINS.md
├── locale/                   # fr_FR and pt_PT catalogues
├── src/postulo/
│   ├── config/               # settings/{base,dev,prod,test}.py, urls, wsgi, asgi
│   ├── core/                 # OwnedModel, scoped querysets, Tag, layout
│   ├── accounts/             # User, Profile, invites                           (M1)
│   ├── resume/               # structured career content                        (M3)
│   ├── documents/            # CV variants, cover letters, uploads, rendering   (M3)
│   ├── jobs/                 # Company, Contact, JobPosting, capture         (M2/M4)
│   ├── applications/         # Application, events, reminders, analytics    (M2/M5)
│   ├── plugins/              # registry, base classes, built-in sources         (M4)
│   └── api/                  # ninja routers, capture tokens                    (M4)
└── tests/
```

## 5. Data model

**Accounts.** `User` (email as identifier, defined before the first migration),
`Profile` (contact block, links, locale, default theme), `Invite`.

**Resume.** Reusable career content, written once and drawn on by every CV variant:
`Experience`, `Education`, `Skill` and `SkillGroup`, `Project`, `Certification`,
`Language`, with ordered `Bullet` children where they apply.

**Documents.**

- `CV` — a named variant such as "Backend EN" or "Data PT", which selects and orders
  resume content through `CVItem`, and may **override bullets per variant** without
  touching the master content.
- `CoverLetter` — a Markdown body with `{{company}}` and `{{role}}` placeholders,
  reusable as a template or bound to a single application.
- `Theme` — the HTML and CSS templates used for rendering.
- `UploadedDocument` — an externally authored PDF or DOCX, versioned and tagged.
- `RenderedDocument` — an **immutable PDF snapshot** taken at the moment of sending.
  This is the piece most trackers get wrong: months later you need to know which CV that
  employer actually read, not what your CV says today.

**Jobs.** `Company` (owner-scoped, so nothing leaks between accounts), `Contact`, and
`JobPosting` (title, location, remote type, salary range, URL, raw and cleaned
description, source, captured and closed timestamps).

**Applications.** `Application` (posting, status, applied date, channel, the documents
actually sent, priority, deadline), `ApplicationEvent` (an append-only timeline: applied,
acknowledged, screening, interview, assessment, offer, then accepted, rejected, withdrawn
or ghosted), and `Reminder`.

Status is stored both as a field and as an event log: the field drives the board, and the
log drives the analytics and never lies about history.

## 6. Self-hosting requirements treated as features

- **Private media.** Uploaded CVs carry a home address and a full employment history.
  Files are served only through an ownership-checked view, using `X-Accel-Redirect` or
  `X-Sendfile` when a reverse proxy is present. WhiteNoise handles static assets only.
- **Export everything.** One command and one button produce a JSON dump and a media
  archive. Data ownership you cannot walk away with is not ownership.
- **Hardened defaults.** `DEBUG=False`, a required `POSTULO_SECRET_KEY`, strict
  `ALLOWED_HOSTS`, secure cookies, a restrictive Content-Security-Policy, a relocatable
  admin path, and invite-only registration.
- **No telemetry**, and no outbound requests except the URL captures a user triggers.

## 7. Plugin architecture

Designed in M0 and built in M4. This is what keeps the browser extension an addition
rather than a rewrite.

- **Contract.** A `JobPostingData` Pydantic schema. Every source — the URL fetcher, a
  third-party plugin, the future extension — produces exactly this and is validated
  identically.
- **Discovery.** `importlib.metadata` entry points under the group `postulo.sources`.
  Installing a package registers a source; core needs no changes.
- **Interface.** `can_handle(url)` and `parse(url, html) -> JobPostingData`, plus a
  declared name and version for the administration listing.
- **Built-in source.** schema.org `JobPosting` JSON-LD extraction, which most large
  boards embed, falling back to readability text extraction and then to a pre-filled
  manual form. Captures always land in a review screen; nothing is saved on a guess.
- **API surface.** `POST /api/v1/captures`, authenticated with per-device capture tokens
  that are hashed at rest, scoped and revocable.
- **Good citizenship.** A single page fetch that the user initiated, honouring
  `robots.txt`. No bulk crawling of job boards.

## 8. Milestones

| # | Deliverable | State |
| --- | --- | --- |
| **M0** | Repository, toolchain, Django skeleton, split settings, custom user model, i18n wiring, CI, documentation | **Complete** |
| **M1** | Accounts and foundations: allauth flows, invitations, `OwnedModel` with isolation tests, private media delivery, the Tailwind and htmx layout | **Complete** |
| **M2** | Jobs and applications: companies, contacts, postings, applications, the event timeline, board and table views, reminders, notes, tags | **Complete** |
| **M3** | Documents: resume content, CV variants with per-variant overrides, cover letters, uploads and versioning, PDF rendering, snapshot on send | **Complete** |
| **M4** | Capture and plugins: URL fetching, the JSON-LD parser, the plugin registry, the review screen, the capture API and tokens, `docs/PLUGINS.md` | Next |
| **M5** | Insights and data ownership: funnel and response-rate analytics, time to response, source conversion, search and filters, export and import | |
| **M6** | Ship: Dockerfile, compose files for SQLite and PostgreSQL, health checks, installation documentation, v0.1.0 | |

Each milestone ends green: migrations applied, tests passing, and a working interface.
M2 is the first point at which the application earns its keep, so nothing before it
should be gold-plated.

**Deliberately after v1:** the browser extension (M4's API is its foundation), LLM
assistance for tailoring, email ingestion, calendar synchronisation, and the French and
Portuguese translations themselves.

## 9. Open assumptions

1. **Docker is untested locally.** The development machine has no Docker CLI, so M6's
   images and compose files will be written to standard practice and validated on the
   target host rather than here.
2. **Development runs on SQLite, and on Windows with Chromium**, because WeasyPrint's
   system libraries are impractical there. Servers and CI use WeasyPrint, which is the
   default. CI installs Pango so that the one test rendering a real PDF actually runs
   rather than skipping.
3. **`django-tasks-db` does not yet declare Django 6.1** in its classifiers, although it
   sets no upper pin and installs and migrates cleanly. Worth re-checking at M5, when
   background work starts to matter.
4. **Translation catalogues depend on contributors.** The application ships fully usable
   in British English; French and Portuguese arrive when someone writes them.
