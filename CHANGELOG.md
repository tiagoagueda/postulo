# Changelog

All notable changes to Postulo are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Changed

- The default time zone is now `Europe/Paris` rather than UTC.
