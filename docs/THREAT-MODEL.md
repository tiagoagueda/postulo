# Threat model

One page, kept current, that every new feature answers to. Postulo stores the most
personal documents a person has while looking for work — CVs with a home address and a
phone number, cover letters, an employment history, the record of who has and has not
replied — and it is usually run by one person on a small machine. That shapes who might
attack it and what is worth defending.

## Who the attackers are

| Attacker | What they can reach | What stops them |
| --- | --- | --- |
| **A stranger on the internet** who finds the address | The sign-in page, the sign-up page if registration is open, the capture API with no token | HTTPS with HSTS; sign-in rate-limited per address and per account; usernames and addresses verified before use; invite-only registration by default; the API answers `401` to everything without a live token and never confirms whether a record exists; `SECRET_KEY` is obligatory in production. |
| **Another account on the same instance** | Their own records, and every page and endpoint with somebody else's id in it | No one sees another person's data unless that person granted it — explicitly, for a stated scope, revocably, and visibly to them at any time. Superusers can see and change any company, contact or listing, including private ones, but never anyone's notes, applications or documents. A company, a contact or a listing is owned by one person or one group and may be shared with a person or a group, read-only or read/write (#896); everything else is owned by one person and never shared. Every queryset goes through `for_user()` or, for those three, `visible_to()`/`manageable_by()`; every view narrows before it looks up, so a record nobody granted is a `404`, never a `403` that confirms it exists; a read-only grant reaches no address that changes a record, and no grant reaches deleting, sharing or giving one away. Group membership is what authorises a group's records, so every member sees who else is in the group, and an administrator's change of members is logged with who was added and removed and needs a recent sign-in; the lookup of a person to share with is limited per account and names an address as it was typed. A person's notes, their decision about a listing, its history, their applications, their appointments and the contacts they add at a company are theirs whatever is shared: what a deletion counts, what a merge names and what the account archive carries are only the reader's own, and a parent or child company is named only to people who can see it. `tests/security/test_isolation_sweep.py` walks every address that names a record, page and API route alike, and asks for somebody else's; `test_sharing_sweep.py` does it again with every record shared read and read/write, and `test_shared_leaks.py` reads every surface as the grantee. *Server settings* shows administrators accounts, never anyone's notes, applications or documents, and Django's admin, when an operator mounts it, holds accounts, invitations and token metadata and no person's records (#368). Administrators also see every account's profile picture, which the People page draws (#795); a member gets a `404` for anyone else's, and the CV photo is never served that way. An administrator is still trusted with the instance, since they install plugins (below): appoint only people you would trust with the database. |
| **A malicious page that is captured** | The capture parser, the fetcher, anything the HTML reaches — and, where the instance and the person have both switched it on, the copy of the page that is kept | Fetching refuses private and local addresses and re-checks every redirect; the page is parsed into one fixed schema that rejects unknown fields; descriptions are capped; nothing is executed; the result is shown for review, never recorded on its own. **A kept source is never served as a page**: it is stored gzipped as text, shown escaped, and downloaded as `text/plain` and an attachment, from no address as HTML and in no frame. A rendering is one of four kinds of picture or a PDF, checked against its first bytes and answered under that kind and no other. One drawn here is drawn by WeasyPrint alone, which has no script engine, through a fetcher that opens nothing, in a process of its own with a clock and a ceiling on its memory; where WeasyPrint cannot run, nothing is drawn. |
| **A malicious page the person is on** while signed in | Postulo's forms, through their browser | CSRF tokens on every state-changing request; the API takes a bearer token and ignores the session; every `next` stays on this host; the content security policy allows no inline script and no third-party origin; `frame-ancestors 'none'`; cookies are `Secure` and `HttpOnly`. |
| **A malicious or broken plugin** | Whatever the plugin's code can do in the process | Plugins are installed by an administrator, not by people using the instance, and every administrator can do it (#368); a plugin that raises is logged and skipped, and one that cannot erase what it holds about a person stops that erasure rather than leaving its rows behind; connected plugins reach the network only through the guarded client with the operator's destination policy; secrets are encrypted with a key the plugin never sees. This is a trust boundary, not a sandbox: install only plugins you have read. An installed plugin runs in the process with the whole database and every stored secret in reach, and a plugin theme is handed full records to draw (#820); the check that a new plugin imports is the one thing run in a child process, and that child gets an allow-listed environment, so the secret key and the database password are not handed to code that has not yet been trusted. A theme's own files (a font, an SVG, an image) are written into its document before the renderer starts, so the renderer still fetches nothing (#163); the path is held to the plugin's package, an SVG to the picture allowlist with its styles turned into attributes, and the size to a ceiling per file and per document; the document preview alone allows a `data:` font (#343). |
| **A plugin's dependencies** | Code arriving from PyPI that nobody chose or read | A catalogue's signature and checksum cover the plugin's own wheel and not its requirements, which are resolved at install time. Only built wheels are installed, so nothing executes a build script on the way in; the constraint file stops any of them moving a package Postulo itself depends on; and everything that arrived is recorded and shown, so an operator can see what is there. A locked catalogue with hashes for the whole resolved set is the remaining piece. |
| **Someone who obtains a backup or a copy of the database** (any administrator can, through *Server settings → Backups*, after a recent sign-in and with a line in the log; `POSTULO_BACKUP_DOWNLOAD=operator` limits it to the operator's own account and `console` to nobody, #810) | Every row, every file | Passwords are hashed; API tokens are stored as hashes, so a copy is not a set of working credentials; connection secrets are encrypted with a key held outside the database (`POSTULO_FIELD_KEY`). A backup archive carries a salted, stretched (scrypt) mark of that key, so that a restore can say whether the secrets it has put back are readable — never the key itself, and not a cheap test of a guessed one (#811); a weak key is still refused at startup, and setting `POSTULO_FIELD_KEY` to a random value keeps `SECRET_KEY` out of every backup. Files are not encrypted at rest: encrypt backups (see the wiki's *Hardening* page). **Someone who can alter a copy of a backup** is stopped by its signature (#772): the manifest and a digest of every file under `media/` and `plugins/` carry a MAC under a key derived from the instance's own, and a restore refuses an archive that does not check out, or that was written under another key, unless `--unverified` says the operator knows where it has been. An archive with no signature is refused the same way (#974), since one whose signature was removed cannot be told from one written before signing; the Restore page says which it is and prints the flag. An unrecognised `POSTULO_BACKUP_DOWNLOAD` is refused at start-up and grants nobody (#978). |
| **Whoever holds the backup destination**: the provider an administrator chose under *Server settings → Backups*, or anyone with its credentials (#325) | Every archive sent there, its name, its size and when it arrived | An archive is encrypted with age to the operator's X25519 recipient before a destination plugin sees it; only the public key is on the instance, so neither the destination nor a copy of this database opens one, and a destination is refused while no recipient is set. Plugin recipients are refused, since age would run a program they name. The manifest a plugin is handed is plain values and is never written beside the archive. The destination's secrets are encrypted under the field key, never drawn back on the page, and kept when left blank. Retention there runs only after a confirmed send and deletes only what the plugin's own `list()` returned and Postulo itself sent, so a lying or confused destination can delete nothing else; a failed send never rotates the local archive. Every send and every remote delete is logged, and so is who changed the destination; every address is staff only and a 404 to anyone else. The plugin is code the administrator installed: its requests are meant to go through `api.client()`, under the destination policy, and the send runs inside a deadline shorter than its claim. |
| **Someone with the token of a browser extension** | The capture API only | Scopes: an extension's token holds `captures` and nothing else, so the worst it can do is fill a review queue the person will decline. It can also ask whether a posting it names is already held and is told the title, the date and a link, never the company, the address or where the person stands with it. Each capture waiting comes with a `posting_key` that groups the captures of one posting, a hash keyed by the instance's key and the account, which cannot be turned back into the address (#711). Where pages are kept it can also send a rendering with each capture, and that is bounded three ways: what one file may weigh, refused before it is read; what one account's kept pages may weigh together; and the days the page of a capture nobody confirmed is kept. It can send a page and cannot read one back, it cannot replace a rendering that is there, and it cannot add to a capture that has been reviewed. Revoke it from Settings. |
| **Someone with the token of a mail client** | Two calls: a brief list of the person's listings, and adding an entry to one listing's history | Scopes: a mail client's token holds `listings:bind` and nothing else, so it reads a title, a company, a place and a state and never the search, and the worst it can do is add entries the person will read. Each is signed with the token's name; its text is bounded and shown as text; a contact or a file it names is looked up among the person's own, with one answer for somebody else's and for none. Revoke it from Settings. |
| **A vulnerability disclosed in a dependency** | Whatever that library does | Dependencies are audited against the advisory databases on every push and weekly; the process for a disclosure is in `SECURITY.md`. |

## What is most worth defending

Not everything here is equally sensitive, and it is worth naming the top of that list rather
than letting it be discovered.

**A home address is the sharpest thing this application holds.** Since #92 Postulo stores
postal addresses as structured rows — street, postcode, town — for the account holder and for
contacts. A leaked application history is embarrassing; a leaked home address is somewhere
somebody can be found. Three consequences, and each is a rule rather than an intention:

- It is an `OwnedModel` like everything else, so another account's address is a `404` and
  never a `403`. No URL names an address, so the sweep of ids in the address cannot reach
  it: `tests/security/test_formset_rows.py` posts another account's row id into the formset,
  and `accounts:remove_address` is in `tests/security/test_isolation_sweep.py`.
- It is **not unique across the instance**, deliberately. Two people at one address is a
  household, and a uniqueness constraint would refuse the second one *and* disclose, in
  refusing, that somebody else on this server lives there. The telephone numbers make the
  opposite trade knowingly, and the message there says so out loud.
- A CV header prints a town and a country and never a street. That is what stops a home
  address reaching a stranger by default, and `Profile.location` staying its own line is the
  mechanism rather than a convention.

**A driving licence is kept as its categories and never as the licence (#691).** A career
entry holds a country, the categories as the fifteen codes of Directive 2006/126/EC (kept by
Directive (EU) 2025/2205), a note of national letters, and two optional dates: what a CV says
and nothing else. The licence **number** is an identifier people are defrauded with and does
nothing for a CV, so it is the most valuable line a breach could hold and there is no column for
it; neither is there one for the photograph, the signature, the residence or the restriction
codes of the Union model's field 12, which are data concerning health (GDPR Article 9(1)) where
they say a driver wears glasses. `tests/test_driving_licences.py` asserts the model's field
list, so a column of that kind fails a test before it is a decision. A category is checked
against the table in `resume/driving.py` by the page, the archive and the candidate file alike.

**A membership can say what Article 9(1) lists (#693).** The career has a section for the
associations, societies and clubs a person belongs or belonged to, and the schema Europass uses
for them names “political parties” and “membership in a union” among its examples; the
GDPR lists political opinions, religious or philosophical beliefs and trade union membership
as special categories of personal data (text as reproduced at gdpr-info.eu). So a membership has
the standing the home address has above: it is an `OwnedModel`, so another account's entry is a
`404` (`tests/security/test_isolation_sweep.py`); it is never written to a log, a metric or an
error message, and no list of another account's or an administrator's shows one; and a CV prints
it only where the person put that entry on that CV. The form says once that a membership of a
union, a party or a congregation is theirs to leave off any CV, and nothing asks for one. There
is no field-level encryption: the data is the person's own, in their own account, covered by the
archive and by deletion, and an instance that must answer to a regulator should confirm this
reading of Article 9 for itself. A membership number is never kept: it identifies the person at
another body, and nobody asked for it.

**A referee is the account holder's data about somebody else, and is printed only when agreed
and chosen (#696).** A reference is a contact, so the instance's export, erasure, retention and
merge reach the person without a second place to look; the entry holds only how they are known,
what has been agreed (a note of the account holder's own, not a consent record) and whether the
details print. A CV prints a reference only when its permission is *Agreed*, and then the name,
the role, the company and the relationship; the email address and the primary number only where
the entry says so. The renderer builds the `sections` a shipped theme draws from under those two
rules, as a read-only value that never holds the entry or the contact, and the themes
Postulo ships read nothing else of a reference. A theme from a plugin is not held to them:
it is handed the CV and letter records themselves, which reach each reference's entry and
contact, and is trusted like the plugin that carries it (#820, #965). Nothing about a
referee is written to a list, a log or an error message, and the candidate file never carries
one: a file moved to another instance should not hold other people's addresses. A CV already
sent keeps the words it was sent with (#217), which the erasure and the merge say.
`tests/test_references.py` and `tests/security/test_cv_prints.py` hold each of these for
the shipped themes.

**A date and a place of birth sit beside it (#679), and so do nationalities (#680) and
gender (#681).** They are what an identity check asks for, a nationality can reveal an ethnic
origin, and gender is personal data that some regulators treat as sensitive, so they are the
most identifying things a profile can hold. They are columns of the account holder's
own `Profile`, optional, never worked out from anything (an age is never computed, stored or
shown; a form of address, pronouns and gender are stored as language-neutral concepts
(`he`, `doctor_f`, `non_binary`), apart from free text typed under *Other* (#921); a nationality or a gender is never guessed from a name, an address, a language or a
telephone number, and a gender is kept off contacts, which would record it of somebody who
never asked) and held to one rule in `core/personal.py`, whichever door a value comes in at: the
page, the API, a candidate file and an archive. The same rules as the address, each with a
test:

- Owner-scoped like every row, and removed with the account.
- Never in a list, a log, a search result, a webhook or an error message. The API reads and
  writes them only through `GET` and `PATCH /profile`, under the `read` and `write` scopes, and
  no list endpoint carries them.
- Printed only where a CV says so: each is a switch on the CV, off by default, so a CV, a
  letter or a file made without choosing prints none of them. A nationality prints as the
  countries listed or, for somebody who would rather say less, as only whether they are a
  citizen of the EU, the EEA or Switzerland.
- A place of birth is the text typed and a country, never looked up and never offered to
  the map, nor to the service that looks up the map's streets (#913).

**Nothing about an address is verified, and nothing about it is a way back in.** Postulo is
not going to post anything, so *valid* means well-formed enough to be used. An address can
never become a recovery route by accident, because there is no flag on it that could.

**The map knows a town or a postcode, unless the operator switches street look-ups on or a
person places an address by hand.** A postal address is placed offline at its postcode's
centroid, where an operator downloaded the postal codes, or else at its town (#108, #700,
#702): a search at street precision is a map of where the person will be at nine in the morning
if any of it works out, sitting in the same database as the CV that carries the home address.
#700 kept every location off geocoding services for that reason; the maintainer reversed it on
2026-10-15 (#913), behind a switch that is off until the operator turns it on. The map is drawn
by Leaflet, served from the instance, over tiles the operator chooses. The tiles and the street
look-ups are the two ways the neighbourhood of somebody's search can leave the instance. What
follows are promises:

- **Street look-ups are the operator's decision, and say what they send.** *Server settings →
  Plugins → Maps*, or `POSTULO_GEOCODER`, chooses a service: Nominatim (OpenStreetMap's public
  instance pre-filled), Photon or addok, each at an address the operator can point at their
  own. The scheduler sends it one address a second: the street, postcode, town, region and
  country of a company's address, and of a person's own or a contact's address where that is
  shown on the map. It never sends whose address it is, and nothing is sent from a page as it
  is saved. People's and contacts' addresses are personal data, and OpenStreetMap's public
  Nominatim asks not to be sent any, so the setting asks the operator's explicit word before
  any look-up runs; set in the environment, the setting is that word. The line under each
  address names the service before its street is sent, the record of processing lists the
  service as a recipient, and a refusal from the service stops every look-up until the
  setting is saved again.
- **A finer pin never overrides a person.** A point placed by hand (#702) or from Wikidata
  (#701) is never replaced by a look-up, and *take off* gives a point back to the address's
  words. A company's addresses are drawn; a person's own home and a contact's address, which
  is somebody else's personal data, only when they ask, address by address, and only those
  are looked up. A point is stored on the instance, is in the contact's data-subject export
  and goes when the contact is erased.
- **A tile server learns the area a person looks at, and the operator chooses which.** By
  default the browser fetches OpenStreetMap's public tiles (#898, reversing #700's choice of
  no tiles): the foundation's servers see the viewer's address and which tiles they ask for,
  which is roughly where that person's companies are, with the instance's origin as the
  Referer and never the page's path. The content security policy opens to that one origin
  on the map page alone. *Server settings → Plugins → Maps*, or `POSTULO_MAP_TILES`,
  switches to a tile server of the operator's own, which on the instance's own origin sends
  nothing anywhere, or to no tiles, the world's outline drawn from the instance.
- **Where the browser is stays in the browser.** *Near me* on the map asks the browser for its
  position only when pressed, moves the map there and sends it nowhere; nothing stores it.
  Every other page is served with `Permissions-Policy: geolocation=()`, so nothing on them can
  even raise the prompt (#850).
- **The map can be switched off.** The `maps` feature plugin decides whether the map, the
  placing of a location and any pin are offered. Off, nothing is shown and nothing is
  deleted: every stored coordinate stays for the day it is switched back on.
- **A grantee who renames a shared company is asked about its owner's other companies
  (#949, accepted and bounded).** A read/write grant lets the grantee edit the record's own
  fields, the name among them, and the per-owner uniqueness of names needs the clash check
  to look at everything the owner holds, which the grantee cannot otherwise see. Dropping
  the check was rejected; so was making the name the owner's alone. The answer is kept and
  bounded as the telephone one is (#758): a person who does not own the company is told that
  a name is held at most `POSTULO_COMPANY_NAME_RATE` times an hour (20 by default), and once
  that is spent a free name and a held one are refused in the same 429, so the refusal is
  no longer the answer. The residue is a slow, per-account guess at employer names by
  somebody the owner trusted with one record, and every rename that succeeds is in the
  record's trail, which the owner sees.
- **A refused capture says why, and so tells a caller what a name resolves to (#954,
  accepted).** A capture of an address whose name does not resolve, or resolves to a private
  or local network, is refused with a sentence for each case, in the API's 422 `detail` and
  on the capture form. A token holder or any account can therefore ask, one hostname at a
  time, whether the instance's resolver knows a name and whether it lives on the private
  side: a slow map of the internal names of the network the instance sits on, bounded by the
  capture allowance, which is spent before the fetch (30 an hour). The project chose to tell
  the person why a fetch was refused, because a bare refusal of a mistyped address or a
  captive portal is worse than the disclosure on a personal instance; on an instance in an
  office, where internal names are worth something, an operator who minds can lower
  `POSTULO_CAPTURE_RATE`. The sentences are the ones the person is owed, and
  `tests/security/test_outbound.py` pins them. Awaiting the owner's confirmation at review.

**A publication's DOI and a person's ORCID leave the instance only at a press (#688).** *Look
up* beside a publication's DOI asks `doi.org` for that one record, which doi.org passes to the
agency that registered it (Crossref's `api.crossref.org`, DataCite's `data.crosscite.org`,
mEDRA's); *Suggest from my ORCID record* asks `pub.orcid.org` for the public works of the ORCID
the person stored on *Your details*, never one typed into the request, so it cannot read
another researcher's list through the instance. What leaves is the identifier, in the path of
a fixed host, and the project's `User-Agent`; no email address, the person's or the
operator's, and nothing else about the account. What the services learn is that this
instance's address asked about that DOI or that ORCID, which Crossref keeps 90 days. Nothing
is asked on a page load, a save, an import, a schedule or a worker, nothing is retried, and
the answer is untrusted text: a short list of fields, stripped of markup, control and
bidirectional characters, cut to each column and drawn escaped, with the abstract, the
references and the record's own links never read into anything. Nothing is stored until the
person saves the form or ticks a work. Each press spends `POSTULO_LOOKUP_RATE` (20 an hour)
whether or not the service answers, through the public-only client with three https redirects
at most, 2 MiB or 4 MiB and ten seconds. The `publication-lookups` feature switches both off;
ORCID's terms bar using its public API in a revenue-generating service, which is the
operator's to judge, and `tests/security/test_outbound.py` and
`tests/test_publication_lookup.py` hold the rest.

**A register lookup tells a register which company somebody is looking at (#1021).** With the
*Register lookup* plugin on, a person who may change a company can press *Look up* beside its
register number or LEI, and the server asks that country's register (France's API Recherche
d'entreprises, Norway's Enhetsregisteret, Finland's YTJ, Czechia's ARES, Poland's KRS) or
GLEIF. What leaves the instance is the number, in the address asked for, with Postulo's user
agent: no name, no address, no cookie, nothing about the person. The register still learns
from the instance's address that somebody there is interested in that company, which on a
personal instance is close to saying who, so the rules are these:

- **Off until an administrator switches it on.** The plugin ships with the instance's row set
  to off (`plugins` migration 0012); the page shows no button and the view answers 404 until
  *Server settings → Plugins* turns it on, for everybody or for chosen accounts.
- **A press, one company, bounded.** Never on a page load, a save, an import or a schedule.
  Each press spends the account's `POSTULO_REGISTER_LOOKUP_RATE` (30 an hour by default),
  before the request, whichever register answers.
- **Only the hosts an adapter declares.** The request is made by Postulo through the
  public-only client (rule 5), with a deadline, a body limit and no redirects, and only to an
  `https` address on a host the adapter named; an adapter from another package
  (`postulo.register_adapters`) builds the address and reads the JSON, and cannot make the
  server dial anywhere else or take a country Postulo already answers for.
- **What comes back is a stranger's text, and nothing is kept unseen.** Every string is
  cleaned of control and direction characters and cut to its column, codes are checked
  against Postulo's own tables, and the review page keeps only what the person ticks. A
  French company whose details are not public (`statut_diffusion` other than `O`) has no
  address offered; Norway's *removed for legal reasons* is said and nothing is held.
- **The record of processing names each register** while the plugin is on for anybody, with
  its hosts, what it is sent and its licence.

`tests/test_register_lookup.py` and the register tests in `tests/security/test_outbound.py`
hold these.

## What is out of scope

- The operator's own machine. Root on the host reads everything; that is true of any
  self-hosted software and no code here changes it.
- Denial of service against a personal instance. A reverse proxy's limits are the right
  place for that, and the wiki says how.
- A compromised browser or operating system on the person's side.
- The database container holding the instance's keys, **on the PostgreSQL profile
  only** (#791, accepted). `docker/compose.postgres.yml` gives the `db` service the whole
  `.env` through `env_file`, because Compose cannot pass it one variable without every
  command carrying `--env-file`. The database image only reads `POSTGRES_PASSWORD`, but
  its environment also holds `POSTULO_SECRET_KEY` and `POSTULO_FIELD_KEY`, so an attacker
  who takes over that container reads the key that decrypts stored connection secrets
  as well as the rows, which weakens "the key is held outside the database" for this
  profile. The SQLite default is unaffected. An operator who minds can pass `db` only
  its password by an `environment:` entry and `--env-file .env` on every command.

## The rules that follow

1. A new model with an owner inherits `OwnedModel`, and a test proves isolation. A new
   relation to a company, a contact or a listing is added to `jobs.detach.DEPENDENTS`, so
   whoever loses one keeps a copy of what their own data points at (#896).
2. A new view narrows its queryset with `for_user()` before any lookup -- or, for a
   company, a contact or a listing, says what it does to it: `SharedObjectMixin` with
   `access` read, write or manage. What a person keeps of their own about a shared record is
   always read for them, never for its owner.
3. A new file is delivered through `serve_private_file`, never by the web server. Text
   that somebody else wrote is delivered through `serve_private_text` beside it, which
   has one media type and no way to be given another: what a stranger's page would do if
   it were drawn from this origin is the reason it is never drawn.
4. A new `next` goes through `postulo.core.redirects.safe_next`.
5. A new outbound request goes through `postulo.plugins.http.client()`, and one that is
   not HTTP goes through `postulo.core.destinations.approve()` — same three rules,
   different protocol: refuse private addresses unless the operator allowed them, check
   every address the name answers with, and connect to the one that was checked. What is
   fetched from a stranger -- a posting, a link, a logo -- also dials the web's ports only
   (`POSTULO_FETCH_PORTS`, #780). `POSTULO_CONNECTIONS_ALLOW_PRIVATE` is a decision of the
   operator for the whole instance, not for one plugin or one account: where it is on, every
   account can map the private network through the Test buttons, and that is accepted, and
   written in `.env.example` and the wiki, rather than hidden (#779). **The one exception is
   OpenID Connect**: discovery, token and userinfo calls are made by allauth's own HTTP session
   and not through the guarded client. The provider's address is an environment setting only the
   operator can write, never a field on a page, so there is no stranger's address for the
   destination policy to refuse; if that address ever becomes configurable from a page it must
   be checked with `check_destination` first (#815).
6. A new secret is stored hashed if it only needs checking, encrypted if it needs using.
   The encrypted set now includes a mail password typed into *Server settings*, under the
   same key as every plugin connection's credentials — which is why #111 made a weak
   `POSTULO_SECRET_KEY` a start-up refusal rather than a warning. It also includes an
   **OAuth refresh token**, which is a longer-lived credential than a password and is not
   changed by changing one: somebody who takes it sends as that person until the grant is
   withdrawn *at the provider*, which Postulo cannot do on their behalf. Since #151 that
   includes the **instance's own mailbox**: the refresh token and client secret for the mail
   that sends password resets sit encrypted on the policy row, and a callback finishing that
   consent is refused unless the signed-in administrator is the one who started it.
7. A new endpoint gets a test in `tests/security/` saying what an attacker would try.
