# The API

Postulo has one machine-readable surface, at `/api/v1/`, and it does exactly what the
token you hand it allows. It began as the *capture API* — a way for something outside
Postulo to hand over a posting, and nothing else — and that part is unchanged. Around it
now sits the rest: reading a search, recording to it, downloading what was sent.

## Tokens and scopes

**Settings → API tokens.** Give it a name — the device or tool it is for — tick what it
may do, choose when it expires, and Postulo shows you the token **once**. Only a hash is
kept: a copy of the database is not a set of working credentials, and a lost token is
replaced, not recovered.

| Scope | What it allows |
| --- | --- |
| `captures` | Hand over a posting for review, and list the captures awaiting review. Nothing else. |
| `read` | Read everything the owner has: applications with their timelines, listings, companies and contacts, reminders, interviews (as JSON or as `.ics`), CVs, letters, the list of files, insights. |
| `write` | Record and change, through the same code as the forms: add listings and apply to them, record applications, change status, add timeline entries and reminders, add companies and contacts, draft letters. Nothing is deleted through the API. |
| `documents:read` | Download the files themselves — uploads and the snapshots of what was sent. Separate, because files are the most sensitive thing here. |

A browser extension needs `captures` and nothing else; a leak costs its holder the
ability to fill a review queue you will then decline. An agent starts with `read` and is
given `write` when you trust it. Every timeline entry written through the API is signed
with the token's name — *via API token laptop-agent* — so you can always see what an
agent did, and undo it by hand.

A token is not a sign-in. It never reaches the web interface, and two-factor
authentication does not apply to it. Revoke it from the same page; revoked and expired
tokens answer `401` like a token that never existed.

## Using it

Send the token as a bearer token. To check one and see its scopes:

```sh
curl -H "Authorization: Bearer YOUR_TOKEN" https://postulo.example.org/api/v1/me
```

Everything the API offers is described in OpenAPI at `/api/v1/openapi.json` — load it into
any client or viewer; there is deliberately no documentation page served by Postulo, since
its assets would have to come from a CDN the content security policy forbids. Lists are
paginated with `limit` and `offset` and come back as `{"items": [...], "count": n}`.

## Capturing a posting

Needs `captures`.

```sh
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://example.org/jobs/42"}' \
  https://postulo.example.org/api/v1/captures
```

Postulo fetches the one page — public addresses only, `robots.txt` honoured — reads it,
and stores a **capture** for review. To capture a posting only a signed-in reader can see,
or one behind bot protection, send the page source yourself and nothing is fetched:

```sh
  -d '{"url": "https://example.org/jobs/42", "html": "<!doctype html>..."}'
```

The response is `201` with the capture: title, company, location, source, status and a
`review_url`. `422` means the page yielded nothing usable, or the address was refused, and
`detail` says which. Nothing is created but the capture: the owner reviews it into a
listing, and applies from there. `GET /api/v1/captures` lists the captures awaiting review.

## The browser extensions

The capture API was built for a browser extension, and there is one — for Chromium-based
browsers (Chrome, Edge, Brave, Vivaldi, Opera, Arc) and for Firefox and its forks,
including Firefox for Android:

- [postulo-chromium](https://source.tiagoagueda.com/postulo/postulo-chromium) holds
  the source, one Manifest V3 codebase built for both browsers.
- [postulo-firefox](https://source.tiagoagueda.com/postulo/postulo-firefox) assembles
  the Firefox package from it and carries what addons.mozilla.org needs.

Set it up once: make a token under **Settings → API tokens** with the `captures` scope and
nothing more, then paste your Postulo's address and the token into the extension's
settings. *Test* asks Postulo who the token is; *Save* asks the browser for permission to
reach your address. From then on, on a job posting, the button (or `Alt+Shift+P`) sends the
page **as your browser sees it** — so a posting only visible to a signed-in reader, or one
behind bot protection, is captured too — and shows what Postulo read, with a link to the
review screen. The page goes to your server and nowhere else: no analytics, no third-party
requests, no permission to run on any site until you press the button.

## Reading

Needs `read`. Everything is the token owner's; another person's records are `404`, as they
are in the web interface.

| Call | What comes back |
| --- | --- |
| `GET /applications?status=&company=&since=&open_only=` | Applications, newest first |
| `GET /applications/{id}` | One application with its timeline, reminders, interviews and sent documents |
| `GET /listings?state=undecided` | Listings; `state` is `undecided` (default), `new`, `shortlisted`, `discarded`, `applied`, `closed` or `all` |
| `GET /listings/{id}` | One listing, description included |
| `GET /companies?q=` · `GET /companies/{id}` | Companies, with their `industries` as a list of names and their `identifiers` as `{scheme, value, label, url}`; `q` matches identifier values too; the detail carries contacts and listing ids |
| `GET /reminders?due=true` · `?outstanding=true` | Reminders |
| `GET /interviews?state=upcoming` · `GET /interviews/{id}` | Interviews; `state` is `upcoming` (default), `scheduled`, `past` or `all`; each carries a stable `uid` |
| `GET /interviews/calendar.ics` · `GET /interviews/{id}/calendar.ics` | The diary, or one interview, as iCalendar text |
| `GET /cvs` · `GET /cvs/{id}` | CVs; the detail lists what each includes |
| `GET /letters` · `GET /letters/{id}` | Cover letters; the detail carries the text |
| `GET /documents?source=` | Files — `upload`, `rendered`, or both when unset — with a `download_url` each |
| `GET /insights` | The figures the Insights page shows |
| `GET /search?q=&limit=` | Everything matching, grouped by kind as the search page shows it, each hit with its passage and `web_url` |

## Writing

Needs `write`. Every write goes through the same services as the forms, so the event log
stays the single truth.

| Call | What it does |
| --- | --- |
| `POST /listings` | Add a listing: `company_name`, `title`, and any posting fields |
| `POST /listings/{id}/apply` | Apply: `status`, `channel`, `priority`, `deadline`, `tags` |
| `POST /listings/{id}/shortlist` · `/discard` (`reason`) · `/restore` | Decide about a listing |
| `POST /applications` | Record an application in one step: the listing fields and the application fields together |
| `POST /applications/{id}/status` | `status`, optional `note` — the timeline records the change |
| `POST /applications/{id}/events` | `kind`, `summary`, `body`, optional `occurred_at` |
| `POST /reminders` · `POST /reminders/{id}/complete` | Reminders |
| `POST /interviews` | Schedule: `application_id`, `starts_at`, optional `ends_at`, `kind`, `location`, `contact_ids`, `notes`, `remind` |
| `PATCH /interviews/{id}` · `POST /interviews/{id}/outcome` | Move or change an interview; record `done`, `cancelled` or `no_show` |
| `POST /companies` · `PATCH /companies/{id}` · `POST /companies/{id}/contacts` | Companies, matched by name as the forms do — or by a Wikidata id in `identifiers`, which wins over the name — and their people; `industries` is a list of names, unknown ones join the vocabulary; `identifiers` is a list of `{scheme, value, label}` (schemes `wikidata`, `lei`, `register`, `linkedin`, `crunchbase`, `opencorporates`, `other`), a malformed or borrowed one is a 422; a PATCH replaces either whole list. `POST /listings` and `POST /applications` take `company_wikidata` beside `company_name` |
| `POST /letters` | Draft a cover letter |

## Downloading

Needs `documents:read`. `GET /documents/{source}/{id}/download`, where `source` is `upload`
or `rendered`; the `download_url` on each document is exactly this.

## Responses

`401` is a missing, mistyped, revoked or expired token — nothing more is said, because
confirming a token exists is itself information. `403` is a live token without the scope
the call needs, and `detail` names the scope. `404` is a record that is not yours, or does
not exist; the two are deliberately indistinguishable. `422` is a value that will not do,
with `detail` saying why.
