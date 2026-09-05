# Changelog

All notable changes to Postulo are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Listings: the stage before applications.** Every posting arrives in *Listings* first,
  whether captured from a page or typed in, and waits as *new* until it is shortlisted,
  discarded (with a reason, kept) or applied to. *Apply* is what creates the application;
  *applied* and *closed* are read from the facts, never set by hand. Reviewing a capture
  now saves a listing rather than an application, with an *I have already applied*
  shortcut for recording after the fact; *Record an application* keeps doing both steps
  in one form. Captures waiting for review sit at the top of the Listings page, and the
  old captures page redirects there. The dashboard counts listings to decide on and those
  closing this week; Insights reports selectivity. The export format is now 2 and the
  importer still reads 1. Existing postings all had applications and show as applied. (#25)
- **Accounts have a username.** Chosen at signup, 3 to 32 lowercase letters, digits,
  dots, underscores or hyphens; it signs in interchangeably with the email address, and
  it is what others on a shared instance see. Existing accounts were given one derived
  from their address on upgrade (`alex.morgan@…` → `alex.morgan`), changeable on *Your
  details*. `createsuperuser` and `changepassword` now take the username. (#1)
- **A full name is obligatory.** The signup form asks for it, *Your details* insists on
  it, and the dashboard asks for it until an account that predates the rule has one. (#2)
- **Email addresses are verified before they are used.** A link is sent at signup and
  the account signs in once it has been followed; an invitation bound to an address counts
  as that proof, and so does `createsuperuser`. An account may hold up to five addresses,
  one of them primary. Addresses in use before this release were marked verified by the
  upgrade, so nobody already signed in is locked out. (#3)
- The header's right side is now the account menu — an initials tile, the name, and a
  disclosure holding *Your details*, *Export everything* and *Sign out* — beside the theme
  switch. *Capture* and *Record* moved to the dashboard, which had relied on the header
  for both. (#10)

### Added

- **Interviews as meetings in a diary.** An interview has a start and an end, a place or
  a link, the people at the company you are meeting, a kind — phone screen, video call,
  on site, panel, assessment — preparation notes, and a stable calendar identifier.
  Scheduling one writes the timeline and makes a reminder for the day before; *Held*
  writes the interview entry dated when it happened and moves a lagging status through
  the usual path; *Cancelled* and *No-show* are recorded too. One that already happened
  is recorded as held from the same form. The dashboard shows *Coming up* and asks about
  interviews that passed without an outcome; the board card and the table show the next
  one. Every interview downloads as an `.ics` file, and so does the whole diary. The API
  gained `/interviews`, application detail carries them, and Insights reports the median
  days to a first interview. The export format is now 3 and the importer reads every
  earlier one. (#14)
- **A general API with scoped tokens.** Capture tokens became API tokens holding any of
  four scopes — `captures`, `read`, `write`, `documents:read` — with an optional expiry;
  every existing token kept exactly the `captures` scope, so nothing installed stopped
  working. `read` covers applications with timelines, listings, companies, reminders, CVs,
  letters, files and insights; `write` records and changes through the same services as
  the forms and signs every timeline entry with the token's name; `documents:read` alone
  downloads files. The OpenAPI description is at `/api/v1/openapi.json`. (#12)
- **Notifications.** Postulo can now tell you things: a reminder falling due, a posting
  arriving through the capture API. A notifier is a connected plugin; the built-in one is
  **Email**, through the instance's mail settings, and every notifier connection carries
  a switch per event. Reminders are noticed by `manage.py send_due_reminders`, run from
  cron or as the Compose `scheduler` profile, and each is announced once.
  `POSTULO_PUBLIC_URL` gives those messages absolute links. (#4)
- **Connections**: the per-person configuration and secrets for plugins that talk to
  another service — notifiers, document stores, synchronisation. A plugin describes its
  fields; Postulo draws the form under Settings → Connections, stores secrets encrypted
  (under `POSTULO_FIELD_KEY`, or a key derived from the secret key), never shows them
  back, and offers a Test button that runs the plugin for real. Plugins get one shared
  HTTP client that enforces the destination policy on every request: private addresses
  are refused unless `POSTULO_CONNECTIONS_ALLOW_PRIVATE` is set. The plugin registry now
  knows four kinds — sources, notifiers, stores, syncs. (#11)
- **Single sign-on through OpenID Connect**, native. Three environment variables name
  the provider and a button appears on the sign-in page; an address the provider has
  verified signs in the account that holds it and links the two, never duplicating. By
  default only existing accounts sign in; `POSTULO_OIDC_AUTO_SIGNUP` lets the provider
  create them. Usernames and names come from the claims, bent to Postulo's rules. The
  callback to register is shown under Server settings → Sign-in; connections are managed
  under Settings → Account. Provider tokens are not stored. allauth's `socialaccount`
  extra becomes a dependency. (#6)
- **Server settings**, for administrators, from the account menu: an overview of what
  is running and where the data is; People, with the invitations, make-administrator
  and deactivate (never the last administrator); the sign-in policy; a test of the email
  settings; the installed plugins; capture policy; and the instance's name, tagline and
  the language and time zone new accounts start with. Policy now lives in the database,
  and an environment variable, when set, still wins and is shown as such — so an existing
  `.env` keeps meaning what it meant. **The first account on an empty instance becomes
  the administrator**, with a trusted address, which makes `createsuperuser` optional.
  Invitations left the main navigation for Server settings → People. (#24)
- **Instance backup and restore.** `manage.py backup` writes one archive holding a
  manifest, the database — through SQLite's backup API or `pg_dump`, consistent while
  Postulo runs — and the media directory, and verifies it. `manage.py restore` puts one
  back onto an empty instance (or, with `--force`, over a populated one), refuses the
  other engine's archive and anything that escapes the media directory, then runs
  migrations. `POSTULO_BACKUP_DIR` is the default destination. (#32)
- **Two-factor authentication**, opt-in per person under Settings → Account: a code from
  an authenticator app after the password, ten single-use recovery codes, and "trust this
  browser" for thirty days. `manage.py mfa_reset <username>` is the way back for an
  account that has lost both phone and codes. Capture tokens are their own credential and
  do not go through it. Through allauth's `mfa` app, which becomes a dependency. (#27)
- A **Settings** area, reached from the account menu: Appearance, Language and time,
  Account (username, addresses, password), Capture tokens and Your data, each its own
  page in a sidebar. allauth's address and password pages appear inside it. *Your details*
  keeps what documents print — the name and the contact block — and nothing else. A plugin
  with per-person settings can register a section (see `docs/PLUGINS.md`). (#22)
- A theme switch in the header, cycling light, dark and match-the-system. It applies at
  once, persists on the profile so it follows the account to every device, and the
  stylesheet now sets `color-scheme` so the browser's own controls follow the theme too.
  The select on *Your details* stays as the explicit version. (#9)
- An icon set. [Lucide](https://lucide.dev) icons (ISC), inlined by a `{% icon %}` template
  tag from files copied into the repository by `npm run sync:icons`, so the application
  needs neither Node nor a network to draw one. Decorative by default; given a `label`
  when an icon stands alone. (#8)
- A browser smoke test of the critical path — sign in, capture by pasting a page, review,
  move the card on the board, record what was sent, download the export — driven by
  Playwright against a live server. Opt in with `uv run pytest -m e2e`; CI runs it in its
  own job on every push and keeps the trace of a failure. (#36)
- `seed_demo`: fills an account with a fictional but believable job search, with
  scripted timelines so Insights has something to say. Deterministic for a given seed.
- `POSTULO_SECURE_COOKIES`, for instances reached only inside a mesh VPN, where the
  browser sees plain HTTP but the wire is already encrypted.
- The commitment that no feature will ever be paywalled, stated in the README, the wiki,
  the contributing guide and the plan.
- Modularity stated as a principle in the same places: an interface wherever a choice could
  reasonably vary, with Postulo's own implementations as plugins that ship in the box.

## [0.1.0] — 2026-09-04

The first release: a job application manager you can run on your own server, from the
applicant's side of the table.

Every milestone from the original plan is in it — accounts, tracking, documents,
capture, insights and packaging. What it is not is battle-tested: it has been used to
record real applications, but by one person, for days rather than months. Treat it as a
first release that works rather than as a mature one.

### Added

- Project skeleton: Django 6.1 on Python 3.12–3.14, split settings, and a custom
  email-identified user model (M0).
- British English as the source language, with French and Portuguese catalogues ready
  for translation.
- Continuous integration on Forgejo: linting, migration checks, tests across three
  Python versions, and a production deployment check.
- Implementation plan covering the architecture, data model, and milestones.
- Ownership foundations: an `OwnedModel` base with an owner-scoped queryset and view
  mixins that narrow rather than check, so another account's record returns 404 instead
  of confirming that it exists (M1).
- Personal profiles holding the contact block that will be printed on CVs, plus
  per-account language, time zone, and theme preferences.
- Invitation-only registration: single-use invitations that expire on their own and may
  be bound to one email address, which is enforced at signup rather than merely
  suggested.
- Private file delivery through an ownership-checked view, with optional hand-off to
  nginx (`X-Accel-Redirect`) or Apache (`X-Sendfile`), and a guard against stored paths
  that resolve outside the media root.
- Interface shell built with Tailwind v4 and htmx, in light and dark themes.
- Companies, contacts and job postings, with postings kept separate from applications so
  that a role you decided against still leaves a record (M2).
- Applications with an append-only event timeline. Status changes are recorded rather
  than merely stored, including from the edit form, so the log can always account for
  the status.
- `Ghosted` as an outcome in its own right: an employer that stops replying is not the
  same as one that says no, and recording it as a rejection would misstate both their
  behaviour and your response rate.
- A board of live applications and a filterable, searchable table of all of them.
- One-page intake that records company, posting and application together, matching
  companies by name case-insensitively.
- Reminders, tags, and a dashboard that leads with what needs chasing.
- Salary figures grouped by the reader's locale rather than a hard-coded separator.
- A career record written once — experience, education, projects, skills, certifications
  and languages — that every CV variant draws on rather than copying (M3).
- CV variants that select and order entries from that record, and may rewrite an entry's
  highlights for one variant without touching the master copy.
- Cover letters with a small, fixed set of placeholders filled in from the application
  they are sent with.
- Uploaded documents, versioned, for files written outside Postulo, delivered only
  through an ownership-checked view.
- PDF export through a pluggable renderer: WeasyPrint or headless Chromium, neither a
  hard dependency. Two themes, Plain and Classic.
- Snapshots of what was actually sent: the PDF frozen at the moment of sending, with the
  text it was built from, never regenerated.

- A user wiki covering installation, configuration, every part of the interface,
  backups and troubleshooting, authored in `wiki/` and published to Forgejo.
- Capturing a posting from its address: Postulo fetches the page, reads what it can, and
  presents the result for review. Nothing is recorded until a person accepts it (M4).
- Two built-in sources: schema.org `JobPosting` structured data, and a fallback reading
  the page's own title and text.
- A plugin interface. Any Python package advertising a `postulo.sources` entry point adds
  a source, with no change to Postulo — see `docs/PLUGINS.md`.
- A capture API with per-device bearer tokens, hashed at rest and revocable, reaching
  captures and nothing else. It is what the future browser extension will use.
- Capturing by pasting the page source, for sites whose bot protection refuses the server
  and for postings behind a login. Postulo fetches nothing in that case.
- Fetch failures now explain themselves. A bare status code is true and useless; a 403
  in particular needs to say that the site blocks non-browsers and what to do instead.
- Insights: a funnel, response rate, time to a reply, and source conversion — every
  figure read from the event log, so an interview that ended in a rejection still counts
  as an interview (M5).
- A complete export: one zip holding a readable JSON document of every record and every
  file, with an import that reads it back into an empty account.
- `export_data` and `import_data` management commands.
- A container image and Compose files for SQLite and PostgreSQL, so installing Postulo
  is no longer a manual job (M6). Built and run on a Raspberry Pi: it migrates, answers
  its health check, serves pages and renders a PDF with WeasyPrint on arm64.

### Changed

- The default time zone is now `Europe/Paris` rather than UTC.
- WeasyPrint is now the default PDF renderer and is installed with Postulo, rather than
  being one of two optional extras. Chromium remains available as a fallback for machines
  where WeasyPrint's system libraries are impractical.
- A backend now counts as available only if it can actually be imported. WeasyPrint is
  installed but unusable without Pango, and detecting it by presence alone would choose a
  renderer that fails at export time instead of falling back to one that works.
