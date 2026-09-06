# Changelog

All notable changes to Postulo are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Three template notes were printing themselves on the page.** Django's `{# #}` comment
  is single-line only; spread over several lines it is text, and the notes above the
  Columns menu, the filters and the table header appeared on every table page with their
  braces. One of them mentioned `<details>`, which the browser took literally, so the
  Columns menu sat inside a stray closed disclosure and could not be reached at all from
  the keyboard. The six offenders are proper comment blocks now, and a test refuses any
  new multi-line `{# #}`. (#41)
- **Small grey text was too faint to pass.** The muted grey used for timestamps, counts
  and hints read at 2.9:1 against white; it now reads at 5:1 in the light theme and keeps
  its lighter value in the dark one, where it already passed. The initials tiles beside
  names use darker backgrounds for the same reason, links inside sentences are underlined
  rather than told apart by colour alone, the search page has a heading and its two search
  landmarks are named, and a posting title in a company's list is a full-height target. (#41)
- Four actions — changing a status from the board, completing a reminder, the *Gone
  quiet* buttons, an interview's outcome — and the career record's move buttons followed
  a `next` parameter without checking it stayed on this site, so a hostile page could
  send a signed-in person elsewhere after a click. Every `next` now goes through one
  helper that refuses another host or a drop to plain http.

### Changed

- **A company can be in several industries.** The one free-text *industry* field became a
  vocabulary of your own — tick what you already use, type new ones, a starter list is
  only suggested — with any number per company. The companies table shows them all and
  filters by any, search matches them, Insights adds a *By industry* table, the API and
  the export carry a list of names (the importer still reads the old string), and
  *Companies → Industries* renames, merges and deletes words. Every existing value was
  converted on upgrade, splitting "Software, Insurance" into two. (#39)
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

- **Postulo speaks every official language of the European Union.** Twenty-three
  catalogues beside the British English source — Bulgarian, Croatian, Czech, Danish,
  Dutch, Estonian, Finnish, French, German, Greek, Hungarian, Irish, Italian, Latvian,
  Lithuanian, Maltese, Polish, Portuguese, Romanian, Slovak, Slovene, Spanish and Swedish
  — each complete as a machine-assisted draft flagged `draft` until a speaker has read
  it, and each saying so in the language picker until then. The catalogues live inside
  the package, so an installed wheel or a container carries them; `scripts/messages.py`
  extracts, checks, compiles and reports on them in plain Python, with no GNU gettext
  needed on the machine, and the build refuses a catalogue that is out of date or a
  translation that lost a placeholder. Pages declare their text direction, so a
  right-to-left language later is a catalogue and not a redesign. (#43)
- **Companies carry external identifiers, starting with a Wikidata id.** A company can
  have one Wikidata item, one LEI (check digits verified), one national register number
  with its country, one LinkedIn, Crunchbase and OpenCorporates slug, and any number of
  named *Other* ids. Paste an address and the id is kept and linked back. One id names one
  company per account, and it is a stronger match than the name: the CSV importer's
  *Wikidata* column, `company_wikidata` on the API's listing and application doors, and
  a restored export all find your company by it whatever it is called. The companies
  table gains a column per kind, search matches the values, the export carries them, and
  the demo seed gives a few companies some. (#42)
- **An accessibility programme with machinery behind it.** The browser tests now run
  axe-core over every page they visit — thirty-odd, signed in and out — against WCAG 2.2
  at levels A and AA, and a violation fails the build naming the element and the rule.
  A *Skip to content* link is the first thing Tab reaches, the main region takes focus,
  and animation respects *prefers-reduced-motion*. The wiki gains an accessibility
  statement: what to expect, what is checked, what is known not to be, and that a feature
  somebody cannot use is a bug. (#41)
- **A security programme with machinery behind it.** A `tests/security` package of tests
  each saying what an attacker would try — sending a person elsewhere through `next`,
  forging a form, calling the API on a session, fixing a session id, guessing passwords,
  reading a token out of the database, climbing out of the media directory with a crafted
  archive, fetching a private file by path — and a threat model in `docs/THREAT-MODEL.md`
  with the rules that follow from it. `SECURITY.md` gains the process for a vulnerability
  disclosed in a dependency, the wiki a *Hardening* page, and CI audits the workflows
  themselves. (#40)
- **A release workflow.** Pushing a `vX.Y.Z` tag refuses to proceed unless `pyproject.toml`,
  `__version__` and `CHANGELOG.md` agree, then builds the sdist and the wheel and creates
  the Forgejo release with that changelog section as its notes and the files attached.
  The image job builds for amd64 and arm64 and pushes to Forgejo's registry, on a
  Docker-capable runner and only when the repository says so. The version now shows in
  the page footer and in `/healthz`, and the Compose files name the published image pinned
  to a minor while `build:` keeps working. (#37)
- **Browser extensions**, in their own repositories: postulo-chromium (Chrome, Edge, Brave,
  Vivaldi, Opera, Arc; one Manifest V3 source built for both browsers) and postulo-firefox
  (Firefox and its forks, Firefox for Android; the package assembled from that source).
  One button, or `Alt+Shift+P`, sends the page as the browser sees it to the capture API
  with a `captures`-only token, and shows what was read with a link to the review screen.
  Only `activeTab`; the instance's origin is requested when it is saved; no analytics, no
  third-party requests. (#17, #18)
- **Import from a spreadsheet.** Under *Settings → Your data*: upload a CSV (any delimiter,
  any encoding Excel produces, or start from the downloadable template), see which column
  Postulo took for which field — guessed from headers in English, French or Portuguese, all
  editable — check a preview of the first rows with dates and statuses as they will be
  read, and import in one transaction. Rows with a date applied become applications, dated
  as the spreadsheet says and marked *Imported from file.csv* on their timeline; rows
  without one become listings; companies are matched by name; duplicates by address or by
  company, role and date are reported, not created. `manage.py import_csv` does the same
  from the command line. (#31)
- **One search box over everything**, in the header, with `/` as its shortcut. It looks
  through listings, applications and their timelines, companies and people, reminders,
  letters, CVs, files, the career record and the text of what was sent — a hit in a sent
  CV says which application it went to and when — and shows results grouped by kind, a few
  per group with the matching passage marked and a link to the rest. Closed and discarded
  things are included. `GET /api/v1/search` gives agents the same. (#29)
- **Delete my account.** Under *Settings → Your data*, with the export offered first: the
  page lists what goes, asks for the password again (or the second factor) and the address
  typed out, and then deletes everything at once — every record, every file behind a
  document or a picture on the disk, tokens, connections and their secrets, pending
  invitations, the account. The same service sits behind *Delete account* on *Server
  settings → People* and behind `manage.py delete_account`. The last administrator cannot
  be deleted by anyone, including themselves. (#33)
- **A password strength meter** under every field where a password is chosen — sign-up,
  invitation, change, set, reset — and never on sign-in. The estimate is zxcvbn's, run in
  the browser (vendored, served from Postulo's own origin), fed with the name and address
  typed above so a password built from them scores low; a four-segment bar with a word
  that a screen reader hears change, and a hint when it is low. Django's rules stay listed
  beneath as the checklist the meter cannot contradict. New passwords must be at least
  twelve characters; existing ones are untouched. (#26)
- **A picture beside your name.** Upload one under *Your details* — decoded, squared,
  re-encoded to 256 pixels and stripped of its metadata, then served privately like every
  other personal file — or tick *Use my Gravatar* and Postulo fetches the picture for your
  primary address once, server-side, keeps a copy and shows that; nothing is fetched while
  pages are viewed, and the content security policy is unchanged. The upload wins over the
  Gravatar; without either, the initials tile stays. Refetched when the primary address
  changes or on demand; the uploaded picture travels in the export. (#7)
- **Plugins carry their own translations.** A plugin package ships a `locale/` directory
  next to its code, laid out as `makemessages` lays it out, and the registry adds it to the
  catalogues Django reads when the plugin loads. Postulo's own catalogues never carry a
  plugin's strings, so a plugin author adds a language without a Postulo release.
- **Administrators can change a username** from *Server settings → People*, with the same
  form and rules as the person's own *Settings → Account*: lowercase, 3 to 32 characters,
  and unique across the instance in any capitalisation, refused in words before anything
  is saved.
- **Two more commitments, stated where the first two are.** Security: the application
  holds the most personal documents a person has while looking for work, so the code and
  the data are kept as secure as the project knows how, with security tests in the suite
  and dependency vulnerability checks in CI on every run and weekly. Inclusion: Postulo is
  meant to be usable by everyone, at its fullest, including people with disabilities, and a
  feature somebody cannot use is a bug. Stated in the README, the wiki, the plan, the
  contributing guide and the security policy.
- **Gone quiet.** An open application that was sent, has had nothing happen for 21 days
  (adjustable under *Settings → Appearance*) and has nothing planned — no reminder ahead,
  no interview in the diary — is *quiet*. The dashboard lists them, longest silence first,
  with *Followed up*, *Snooze* (a reminder two weeks out) and *Ghosted*; the board card
  says how long; the table filters by it; the API takes `?quiet=true`; Insights counts
  quiet applications per source and names the companies. A notifier with *Applications go
  quiet* switched on hears about newly quiet applications from the scheduler, once per
  silence. The dashboard's *Worth chasing* block, which only looked at the date applied,
  is replaced by this. (#30)
- **Tables you can sort, narrow and arrange.** Applications and Companies now sort by any
  column from its header, narrow by typing beneath the header — text, dates from and to,
  choices — with the table updating as you type and a plain button for scripts-off, and
  offer a *Columns* control to choose which columns show, in what order, and how many
  rows a page holds. Applications gained optional columns for deadline, priority, channel,
  salary, tags, last activity, next reminder, next interview and date recorded; companies
  for people, website, careers page, notes, last activity and date added. Sort and filters
  live in the address; the layout is saved to the account. The defaults reproduce the old
  tables exactly. (#20)
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
