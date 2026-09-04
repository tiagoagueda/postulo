# Changelog

All notable changes to Postulo are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

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
