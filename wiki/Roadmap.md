# Roadmap

Postulo is pre-alpha. This page says plainly what exists and what does not, so nothing on
this wiki reads as a promise.

## Done

| Milestone | What it brought |
| --- | --- |
| **M0** | Project skeleton, tooling, continuous integration |
| **M1** | Accounts, invitations, ownership separation, private file delivery, the interface |
| **M2** | Companies, contacts, postings, applications, the event timeline, board and table, reminders, tags |
| **M3** | The career record, CV variants, cover letters, uploads, PDF export, snapshots of what you sent |

## Still to come

**M4 — capture and plugins.** Paste a posting URL and have the title, company and
description filled in for you, with a review step before anything is saved. A plugin
interface so other people can add support for particular sites, and an API that a browser
extension can use later.

**M5 — insights and data ownership.** Response rates, time to reply, which sources
actually convert, and a one-click export of everything you have.

**M6 — packaging.** A container image and Compose files, so installing Postulo stops being
a manual job. See [Installing Postulo](Installing-Postulo) for what that involves today.

## After version 1

- A browser extension, built on M4's API.
- Optional assistance from a language model for tailoring, as a plugin, disabled by
  default and never required.
- Email ingestion and calendar synchronisation.
- French and Portuguese translations. The application is written in British English and
  the catalogues are ready; they need people to write them. See
  [Translating](https://source.tiagoagueda.com/tiagoagueda/postulo/src/branch/main/docs/TRANSLATING.md).

## Things that are not planned

- Applying to jobs on your behalf.
- Scraping job boards in bulk. URL capture fetches one page that you asked for.
- Any telemetry.
