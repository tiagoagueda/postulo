# Roadmap

**The current release is 0.2.0.** Releasing is a deliberate act here rather than something
that happens on a schedule, so `main` is sometimes ahead of the last tag; the
[changelog](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/CHANGELOG.md)
says by how much.

This page says plainly what exists and what does not, so nothing on this wiki reads as a
promise.

## Released

| Milestone | What it brought |
| --- | --- |
| **M0** | Project skeleton, tooling, continuous integration |
| **M1** | Accounts, invitations, ownership separation, private file delivery, the interface |
| **M2** | Companies, contacts, postings, applications, the event timeline, board and table, reminders, tags |
| **M3** | The career record, CV variants, cover letters, uploads, PDF export, snapshots of what you sent |
| **M4** | Capturing a posting from its address, the plugin interface, and the capture API |
| **M5** | Insights read from the timeline, and a complete export you can import back |
| **M6** | A container image and Compose files, and the 0.1.0 release |

The lettered milestones gave way to numbered releases after that.

## 0.2.0

Forty-four issues, released on 7 September 2026:

- **The twenty-four official languages of the European Union**, each under its own name,
  with the interface saying how far along each catalogue is. See [Translating][translating].
- **Passkeys**, and single sign-on that an operator can let count as the second factor.
- **Security work found by auditing the code**: the link checker no longer follows a
  redirect onto a private address, rate limits are shared between workers rather than
  counted three times, forwarding headers are believed only from proxies you have named,
  and a connection is made to the address that was approved rather than to whatever DNS
  says a moment later.
- **The dashboard and Insights are one page**, built from seventeen widgets you arrange
  yourself. See [Insights and the dashboard](Insights).
- **Importing a Europass CV**, in either the current JSON or the legacy XML.
- **ORCID and other identifiers** on your details, **phone numbers with their country**,
  **a log an administrator can read** and **Prometheus metrics**, both off unless asked for.

## In progress

| Milestone | What it is for |
| --- | --- |
| **0.3.0** | Right-to-left layout (done), the languages of Africa, parent and child companies, and reports on how regularly the search is going |
| **0.4.0** | The languages of Asia and South America, and the fonts to draw them |
| **0.5.0** | The rest of the world's languages, and the tooling that lets a speaker add one without a developer |
| **0.6.0** | Readable and usable on a phone, rather than merely rendered on one |

The [issue tracker][issues] is the authority. This table summarises it and will sometimes
lag it; the tracker never lags itself.

## Already built, as separate repositories

These were once "after version 1" and are not any more. Each is a plugin or an extension
with its own repository, installed only if you want it:

| | |
| --- | --- |
| [Chromium extension][chromium] and [Firefox extension][firefox] | One button that sends the posting you are looking at to your instance, over [the capture API](The-capture-API) |
| [postulo-imap][imap] | Reads one folder of a mailbox and suggests what it says happened — acknowledgements, rejections, invitations. Nothing is written on a guess |
| [postulo-dav][dav] | Company contacts to a CardDAV address book, interviews to a CalDAV calendar, both ways |
| [postulo-apprise][apprise] | Notifications through Telegram, ntfy, Discord, Matrix, Signal, email and a hundred more |
| [postulo-paperless][paperless] | Every rendered CV, letter and upload filed into a Paperless-ngx archive |
| [postulo-mcp][mcp] | Lets an AI agent read, and if you allow it record, your job search — through Postulo's own API. No model inside Postulo |
| [postulo-templates][templates] | A curated set of CV and letter templates |
| [postulo-helloworld][helloworld] | The smallest plugin there is, to copy |

## Still wanted, not scheduled

- Optional assistance from a language model for tailoring, as a plugin, disabled by default
  and never required.
- Tagged PDFs, so a CV made here is one a blind recruiter can read with a screen reader.
  See [Accessibility](Accessibility#known-gaps).
- Speakers to review the machine-drafted translations. Every European Union catalogue is
  complete and every entry in it is marked as a draft until a person has read it, which is
  the honest state rather than a finished one. See [Translating][translating].

## Things that are not planned

- Applying to jobs on your behalf.
- Scraping job boards in bulk. URL capture fetches one page that you asked for.
- Any telemetry.

[issues]: https://source.tiagoagueda.com/postulo/postulo/issues
[translating]: https://source.tiagoagueda.com/postulo/postulo/src/branch/main/docs/TRANSLATING.md
[chromium]: https://source.tiagoagueda.com/postulo/postulo-chromium
[firefox]: https://source.tiagoagueda.com/postulo/postulo-firefox
[imap]: https://source.tiagoagueda.com/postulo/postulo-imap
[dav]: https://source.tiagoagueda.com/postulo/postulo-dav
[apprise]: https://source.tiagoagueda.com/postulo/postulo-apprise
[paperless]: https://source.tiagoagueda.com/postulo/postulo-paperless
[mcp]: https://source.tiagoagueda.com/postulo/postulo-mcp
[templates]: https://source.tiagoagueda.com/postulo/postulo-templates
[helloworld]: https://source.tiagoagueda.com/postulo/postulo-helloworld
