# Changelog

All notable changes to Postulo are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### 🔒 Security

- **WeasyPrint 70, for CVE-2026-55073, and renderers that fetch nothing a document does not
  carry.** The advisory, published on 9 September, is two `write_pdf` arguments —
  `stylesheets` and `xmp_metadata` — that ignored the document's URL fetcher and read local
  files or internal addresses into the PDF. Postulo passes neither, so it was not exposed
  that way, and **both released versions are affected all the same**: v0.2.0 and v0.2.1 lock
  WeasyPrint 69.0, and the dependency audit refuses them from that day. The requirement is
  now `weasyprint>=70`, so an installation cannot resolve to the old one either.

  Postulo had set no fetcher at all, so WeasyPrint's default applied — every protocol,
  redirects followed — and nothing came of it only because every template it ships inlines
  its CSS and embeds what it shows. That is now enforced rather than assumed, before a theme
  from a plugin puts markup nobody here wrote in front of the renderer: WeasyPrint may fetch
  `data:` addresses and nothing else, and Chromium refuses every request the page makes. A
  stylesheet, image or font a document points at by address is left out of the PDF instead
  of fetched.

  And the suite now draws real PDFs: every kind of document in every theme, the report, and
  a document set right to left. Until this it rendered every PDF through a stand-in, so an
  upgrade of the renderer — a new major version every release — was covered by nothing.

  **Installing without the image?** Add `libharfbuzz-subset0` beside the Pango packages.
  WeasyPrint 70 warns at start-up without it and says a later version will require it; the
  image, CI and the install instructions all have it now. (#163)

- **Mail that Microsoft 365 and Google will still accept, before Microsoft stops accepting
  the old kind.** Microsoft switches SMTP AUTH basic authentication off by default for
  existing Exchange Online tenants at the end of December 2026. On the code before this, an
  instance sending through Microsoft 365 would lose the ability to send password resets on
  that date, with no configuration that fixed it — which is people locked out, not a
  modernisation. XOAUTH2 now works on both halves of the mail plugin: the instance's own
  transport and a person's own outbox.

  **It takes no dependency.** XOAUTH2 is one SASL string carrying a username and a bearer
  token, and `smtplib` already speaks SASL; a provider's SDK on the path that delivers
  password resets would have been a large dependency and somebody else's HTTP client to
  solve a dozen lines. The subtle part is that `smtplib` base64-encodes what it is handed,
  and a string encoded twice is refused with a message that says nothing about why — so
  that is tested on the wire rather than asserted.

  **Two grants for the instance, because two different people are consenting.** *Signed in
  once* is an operator agreeing on the provider's page as the mailbox that sends, which
  needs no administrator of anything and works at both providers. *The application sends on
  its own* is Microsoft's client-credentials route, with nobody's session involved, which
  suits a server better but needs a tenant administrator. Google's equivalent is a
  different grant again, so the page refuses that pair before it is saved rather than
  letting it fail at the first password reset.

  **A token can stop working on its own, and a password cannot.** A provider may withdraw a
  grant when a password changes or a mailbox goes unused. When it does, the send fails with
  the provider's own words and is counted like any other mail failure — so the recovery lock
  that keeps email switched on while it is the only way back into an account stops counting
  email as that way once it has failed, instead of going on believing in a route that has
  quietly closed. A consent that comes back without a refresh token is refused on the spot,
  because it would work for an hour and then stop for good.

  **The instance's consent comes back through the address a person's does**, so an operator
  registers one address, not two; the two are told apart by a signing salt each, so neither
  can be passed off as the other, and only the administrator who started one can finish it.
  The client secret and the tokens are kept encrypted under the same key as every other
  secret, never in a column or a cache. **Nothing removes the password**: a relay of your
  own, Mailcow, Postfix and Gmail with an app password sign in exactly as they did, and an
  instance that never opens the Email page behaves exactly as it did. (#151)

- **The image is scanned now, by the project rather than by somebody remembering.**
  Everything in the two issues beside this one was found by running Trivy and Grype by hand,
  on a machine that happened to have them pulled for something else. Neither finding was new
  and both had been in the image since it was built — which is the argument: `pip-audit`
  covers Postulo's own Python lock, and says nothing about the base image, the apt packages,
  what `uv sync` actually installed, or anything left behind in a layer.

  It runs before the push, because a gate after one is a report about something already
  published. And it runs the same `scripts/scan-image.sh` a person runs, rather than a second
  description of the same intent that can drift from the first.

  **Two choices in how it is set up are what decide whether it survives.** Both scanners, not
  one: on the image that started this they disagreed usefully, Grype finding the only
  actionable Debian update that Trivy did not mark fixable, Trivy finding Python packages and
  a leftover cache that Grype's scan did not see at all. And the gate is *fixable* findings
  rather than severe ones: six CRITICALs with no fix available is the ordinary state of a
  Debian base image, and a build that fails daily for reasons nobody can act on is switched
  off within a fortnight.

  The unfixable half is recorded rather than dropped, together with the secret and
  misconfiguration scans that came back clean — that half is what nobody writes down, and it
  is what says the image is in the state you think it is. Each run also keeps a CycloneDX
  bill of materials, which is worth more to somebody self-hosting Postulo than this run's
  verdict: it lets them scan the release next year, against a database that does not exist
  yet. (#156)

- **A telephone number now has a verified state, and on this release nothing is verified.**
  That is the point rather than a shortfall. Numbers have been storable since they arrived,
  unique across the instance and first-come-first-served, with no way to prove one — which was
  fine while nothing depended on them. It stops being fine the moment a number can get somebody
  back into their account: every number typed before that day is a claim nobody checked, and
  whoever typed a stranger's number a month ago would be pre-positioned.

  **So the migration adds a column and nothing else.** No existing row is promoted. A test
  asserts the migration contains a single `AddField`, because granting verification
  retroactively is the kind of thing that looks like a convenience and is a way into somebody's
  account.

  **Proving a number means sending a code to it**, which needs a channel that can reach one, and
  Postulo has none. That is a strict order rather than a preference: no channel, no
  verification; no verification, no way back in. `record_verified()` exists and nothing calls it.

  Three rules are settled now so they are not settled under pressure later. A proof **lapses
  after a year**, because people give up numbers and carriers reissue them — an address is
  forever in a way a number is not, and a stale proof describes somebody who may no longer
  answer there. **Editing the digits forgets the proof**, dropped in `save()` rather than in a
  form, so a row written by the API or a shell cannot keep a verification that was never about
  the number now stored. And only numbers on the account's **own profile** are candidates: a
  recruiter's switchboard is a number this account owns and never one it is, which is a query
  and not a field because the holder is generic.

  **An archive cannot carry a claim of verification in.** The export writes the date, because it
  is the person's own record; the import reads it and throws it away, with a comment saying so,
  because a claim made by another instance is one this one never checked. `FORMAT_VERSION` is 6.

  **Being told a number is already here is now rate-limited.** The disclosure is unavoidable —
  instance-wide uniqueness cannot be enforced without it — and that was decided and written down
  when numbers arrived. What changes once a number identifies an account is the rate: the same
  honest sentence, asked five hundred times, is a list of which numbers have accounts here,
  which is the shape of email enumeration. Twenty an hour, and then Postulo says when the answer
  is available again rather than pretending the save failed for some other reason. Only the
  informative answer is charged for, so recording your own numbers never meets it.
  `POSTULO_NUMBER_RATE` sets it; empty switches it off. (#142)

- **A mail server is a host the server dials, and nothing checked where.** Postulo already
  guards a URL somebody typed with three careful rules, and none of them were on the path a
  mail backend takes: Django opens a socket to a host and a port and asks nothing at all.
  That was fine while the host could only be the operator's own. It stops being fine the
  moment a person can type one, and the guard has to exist before the field does.

  All three rules now apply to SMTP. **Private and loopback addresses are refused** unless
  the operator has set `POSTULO_CONNECTIONS_ALLOW_PRIVATE` — the switch that already lets a
  connection reach a Paperless on the LAN, because a mail relay on the same LAN is the same
  case and one decision made once beats two. **Every address the name answers with is
  checked**, not the first, since a name resolving to one public and one private address is
  otherwise a way straight through. And **the connection goes to the address that was
  checked** — the rule people leave out, and the one that matters: a name with a one-second
  lifetime can answer publicly for the check and privately a moment later, so resolving again
  at connection time would pass on an address nobody ever contacted. The certificate is still
  proved against the name that was typed rather than against a number.

  **`POSTULO_EMAIL_HOST` is exempt**, and that is the project's ordinary environment-wins rule
  rather than a hole in this one: it is a line in a file only the operator can edit, and the
  default it carries is `localhost`. Checking it would refuse the default configuration of
  every instance that has never opened the Email page.

  **The refusal is decided before anything is dialled.** That is what keeps the *Test* button
  from being a port scanner with a form around it: connect and read the banner and you learn
  whether something is listening, so every private address gets an identical answer, because
  nothing ever finds out. A test asserts that four different private addresses produce one
  sentence between them — a different sentence for any of them would be a map of the network.

  `docs/THREAT-MODEL.md` gains the rule, and records that the encrypted-secret set now
  includes a mail password typed into the interface. (#148)

- **The image never took Debian's security updates, so a fixable High sat in it.**
  `libpcre2-8-0` had an update in bookworm that the image did not have. It arrives with
  Python rather than with anything Postulo installs, so no line in the Dockerfile would ever
  have touched it: the build installed what it needed and kept whatever the base image was
  built with, for as long as the base image went unrebuilt. It was the only genuinely
  actionable operating-system finding across two scanners, and it had been there since the
  image was first built.

  `apt-get upgrade` now runs at build time, in both apt layers — the second one matters,
  because an image built with `POSTULO_EXTRA_PACKAGES` refreshes the index again and must not
  end up quietly less patched than one built without plugins.

  **This costs reproducibility and the trade is deliberate.** Two builds of the same commit a
  week apart are no longer the same image. The alternative — pinning the base image by digest
  — buys that back, and makes Debian's updates arrive only when somebody remembers to bump
  the digest. A manual step nobody performs is precisely how this finding got here. Between an
  image that drifts towards being patched and one that reliably stays unpatched, Postulo takes
  the first; each release is published by digest, so the artefact anybody runs is still named
  exactly.

  Four tests read the Dockerfile and hold the ordering, because order is the whole of whether
  the upgrade does anything: before `update` it runs against the stale index that caused this,
  after `install` the packages just installed came from the old one, and in a `RUN` of its own
  it becomes a layer that is served from cache exactly when the index it needed had changed.
  One of them fails if the base image is ever pinned by digest without that decision being
  revisited.

  **Most of what a scanner reports here has no fix and that is the normal state of Debian
  stable.** So the documented gate is *fixable* findings rather than severity: a pipeline that
  fails on CRITICAL fails every day for reasons nobody can act on and is switched off within a
  fortnight. `CONTRIBUTING.md` records the two scanner invocations, why both are run, and why
  they disagree about severity; the pipeline that runs them is #156, which waits on a runner
  that can build an image at all. (#155)

- **`POSTULO_SECRET_KEY=changeme` started an instance perfectly well.** The production
  settings refused to start with *no* key and said nothing about a bad one. Django notices —
  `security.W009` is exactly this check — but the container runs `check --deploy --fail-level
  ERROR` and W009 is a **warning**, so it printed one line into a start-up log nobody reads
  while the instance served traffic. Not a deliberate exemption: the fail level was sitting
  one step above where the check was.

  It matters more here than in most Django applications, and that is the whole of why this is
  tier 1. That key does not only sign sessions and password-reset links: `plugins/secrets.py`
  derives the Fernet key protecting **every stored connection credential** from it — somebody's
  Telegram bot token, their Paperless password, their Nextcloud login — through a single
  unsalted SHA-256. A guessable secret key is a guessable encryption key for other people's
  passwords to other people's services, and guessing is cheap.

  So a key that is not one now stops the instance before the first request: shorter than 50
  characters, fewer than 5 distinct characters, or an obvious placeholder — `changeme`,
  `secret`, anything beginning `django-insecure-`. The two numbers are read out of Django
  rather than retyped, so the day it revises them this disagrees loudly instead of quietly
  enforcing the old ones. **`POSTULO_FIELD_KEY` is checked the same way**, which the issue did
  not ask for and is the same hole by a shorter path: whenever it is set, *it* is the key
  protecting those credentials.

  **Every refusal says the thing that stops it causing a worse problem.** The obvious
  response — generate a new key — signs everybody out *and* makes every stored credential
  unreadable, because the key that encrypted them is gone. The message says so, and says the
  order that keeps them: set `POSTULO_FIELD_KEY` to the current key first, then change the
  signing key underneath it. `POSTULO_ALLOW_WEAK_SECRET_KEY=true` starts a short key anyway,
  for the instance this catches at an hour when nobody wants to plan a rotation — it buys an
  afternoon, it is not an answer, and it does not open for a placeholder, because somebody who
  typed `changeme` has not chosen anything there is time to be bought for. (#111)

- **Nothing bounded how often an account could make the server fetch a URL, call the API, or
  scrape the log.** None of it was reachable by a stranger — every surface needs an account or
  a token, and an audit confirmed the boundaries hold: capture refuses private addresses and
  revalidates on redirect, the API looks a token up by hash, `/logs` and `/metrics` compare
  their token with `hmac.compare_digest`. What was missing was a ceiling on somebody who had
  got past all that legitimately. **Capture is the one that mattered**: it is the only thing
  in Postulo that makes *your* server issue an outbound request to an address somebody else
  chose, and one account could ask for that as fast as the machine would go — which turns a
  self-hosted box into a modest scanner, or exhausts its own outbound connections.

  Three limits now, keyed on the account rather than the address, because an account is the
  thing being limited and sharing an office network should not mean sharing an allowance:
  `POSTULO_CAPTURE_RATE` (30/h, the tightest), `POSTULO_API_RATE` (600/h, **per token**, so
  one handed to something that misbehaves can be revoked without touching your own), and
  `POSTULO_ENDPOINT_RATE` (120/h, per address, since a shared token guards those and there is
  no account to count against). All raisable — three people and three hundred want different
  numbers — and any of them can be set empty to switch off. Anything unreadable also means no
  limit, deliberately: a mistyped rate should leave an operator with a working instance rather
  than a locked one.

  Written against the cache that already backs allauth's limits rather than by adding a
  dependency: it is thirty lines, and a rate limiter is not where a self-hosted application
  should acquire a supply chain. A refusal is a 429 carrying `Retry-After`, so a well-behaved
  client waits instead of hammering. The window is fixed rather than sliding and the count is
  not strictly atomic on the database cache — both stated in the module, and both erring
  towards letting somebody through, which is the right way round for a limit whose job is to
  stop a machine being ridden rather than to meter billing. (#112)

- **Django's admin is off unless you ask for it, and rate-limited when you do.** ⚠️ **This
  removes `/admin/` from instances that have one.** `POSTULO_ADMIN_URL` used to default to
  `admin/`, directly under a comment reading *"the admin is a small attack surface worth
  moving off a guessable path"* — so every instance whose operator had not read that line
  published Django's own username-and-password form at the first address anybody would try.
  It was found on the public test instance rather than read out of the source, which is the
  only way that kind of thing is ever found. The default is now empty and nothing is mounted:
  choosing to run the admin and choosing where it lives became one decision, made once, on
  purpose. **To keep yours, set `POSTULO_ADMIN_URL` to a path of your own and restart.**

  Off by default rather than merely moved, because Postulo's own *Server settings* already
  covers people, sign-in policy, plugins, email, logs and defaults. What is left is a
  developer's convenience, and on a self-hosted box mostly a second and less careful way into
  the same data.

  **And throttled when it is mounted.** allauth's rate limits are good ones and Postulo
  inherits them, but they apply to allauth's views; `django.contrib.admin` has a login view
  of its own and nothing was counting attempts against it. So the one credential form on the
  instance with no attempt limiting was the one that reaches every table directly. It now
  uses allauth's own limiter, cache and numbers — `10/m/ip, 5/300s/key`, the same as a failed
  sign-in — configured as one more key in `ACCOUNT_RATE_LIMITS` rather than a second scheme
  that can drift from the first. A `GET` still costs nothing: reading the form is not an
  attempt, guessing is.

  Two smaller things went with it. A path written without its trailing slash used to produce
  a URL nobody could reach and no error saying why; it is tidied now. And *Server settings →
  Overview*, which linked to the admin as "the escape hatch", says plainly that there is not
  one and which variable turns it on, rather than linking to a 404. (#116)

### ✨ Added

- **A dev channel: every push to `main` publishes an image, so a change can be run before it
  is released.** `dev-image.yml` builds, scans and pushes `:dev` alongside a pinnable
  `:<version>-dev.<short sha>` — quote the pinned one in a bug report, because `:dev` moves
  and says nothing about what somebody was running. It is not a release: unsupported, and
  free to change a database in ways a release will not.

  **`:latest` is never touched**, which is the whole reason this is a separate workflow
  rather than a second trigger on `image.yml`. That one exists to build a *release*: it
  checks out a tag, because "a release image built from a moving branch is an image nobody
  can reproduce", and it moves `:latest`. This one is the opposite in both respects, and the
  release path is worth leaving alone.

  **The scan gate applies.** A dev image somebody runs against their own applications is an
  image, and #155 and #157 were both found in one built and published without a scan. It
  calls the same `scripts/scan-image.sh` a person and a release both call. Built for the
  native architecture only: the release does `linux/arm64` through QEMU, which is slow and
  one more thing to fail, and anybody needing another architecture wants a release.

  **Why this may run on a push when the release image may not.** `image.yml` is
  `workflow_dispatch` only because `runs-on: docker` reaches a daemon that is root on the
  host, and nothing but "nobody pressed the button" stopped a job getting there. That is no
  longer what protects it: the runner answering the `docker` label is a **second runner
  instance registered to this repository alone**, so Forgejo will not schedule another
  repository's jobs onto it — enforced by the instance rather than by every workflow author
  remembering. The workflow says the same thing twice, in the trigger and in an `if` on the
  branch, because a branch filter is one edit away from being wider than somebody meant.

  **The first run found a fault in the release path too, and fixed it.** Both workflows
  built the registry address as `${GITHUB_SERVER_URL#https://}`. `GITHUB_SERVER_URL` is the
  address the *runner* reaches Forgejo at, which on a compose deployment is the internal
  `http://server:3000` — so the strip left the scheme on, and the daemon that pushes runs on
  the host and cannot resolve a compose name anyway. `image.yml` has carried the same line
  since it was written and has never run, so it had never shown it. Both now read a
  `REGISTRY_HOST` repository variable and fall back to the server URL with either scheme
  stripped.

  **And a second one: the shell scripts were not executable.** `scripts/scan-image.sh` and
  `scripts/check-image.sh` were recorded as `100644`, so `./scripts/scan-image.sh` — which
  is how `CONTRIBUTING.md` tells a person to run it, and how both image workflows call it —
  failed with *Permission denied* on any Linux checkout. They were written on a machine with
  `core.fileMode false`, so the bit was never recorded and nothing that ran them had ever
  run from a fresh clone.

  **And a third: the pinned Trivy did not exist.** `scripts/scan-image.sh` pinned
  `aquasec/trivy:0.68.0`, and there is no such tag on Docker Hub — there never was. The
  findings behind #155 and #157 came from running the scanners by hand on a machine that
  already had Trivy pulled, so the pin was never the thing that fetched it. Corrected to
  `0.74.0`, with the one-line check for whether a tag exists written beside it. Grype's pin
  is real and eighteen versions behind; left alone on purpose, since a newer scanner is a
  gate that starts failing for reasons unrelated to the change being made.

  **And a fourth, the subtlest: the scan reports went to the host.** `scan-image.sh` gave
  the scanner containers `-v "$OUT:/out"`. A bind mount is resolved by the **daemon**,
  against the host's filesystem — and this script now runs *inside* a container with the
  socket mounted in, so the scanner wrote its reports into a directory on the host that the
  caller could not see, and every `cat` after it failed. It worked on a laptop, where the
  shell and the daemon share a filesystem. Trivy writes to stdout now, which grype already
  did, and both mounts are gone.

  **The first scan then found something real, and it is gone.** The only fixable High in
  the image was `msgpack` 1.1.2 — **pip's vendored copy**, arriving with
  `python:3.14-slim-bookworm`. Not in `uv.lock`, not in the virtual environment, not
  importable by Postulo, so no dependency bump reached it. **pip is removed from the
  runtime image**: nothing there needs it, because `plugins/installing.py` prefers `uv`,
  which the image already carries deliberately. The pip branch of `installer()` stays — it
  is the right answer for an ordinary `pip install postulo` on somebody's server — and it
  now says plainly what is wrong if it ever finds neither, instead of producing
  *No module named pip* from a subprocess.

  **The dev image builds for arm64 as well**, reversing the native-only decision above for
  a concrete reason: the instance these images are run on is a Raspberry Pi, and an
  amd64-only dev image is one nobody can deploy. QEMU binfmt is registered on the runner, so
  the second architecture costs time and nothing else.

  **And a fifth: the image name carried a capital.** `GITHUB_REPOSITORY` is
  `Postulo/postulo` on this instance, and a Docker repository name may not have one, so the
  tag was refused outright — after a full multi-architecture build. Lowercased in both
  workflows.

  Five faults, none of them in this workflow, all of them in the release path, and all
  found by the simple act of running it. That is what a channel nobody had ever exercised
  was hiding — and two of them needed only a file to be *read*, not run, so
  `tests/test_shell_scripts.py` now reads every tracked shell script: it parses under
  `bash -n`, git records it executable, and it starts with a shebang.

  **And old dev images are pruned.** Every push adds a pinned tag, and nothing ever took one
  away. The job now ends by keeping the newest five and removing the rest — together with
  the per-architecture manifests only they referenced, because a manifest nothing names
  still holds its layers — and nothing else: releases, `latest`, `dev`, and anything
  untagged the run did not itself orphan, such as a push in flight from another workflow,
  are never candidates. `scripts/prune-dev-images.py` does it, and does it by hand with
  `--dry-run`.

  `CONTRIBUTING.md` § *Giving a runner the `docker` label* said to declare `docker:host` and
  **was wrong** for a containerised runner: `host` runs the job inside the runner container,
  which is Alpine with no node and no docker CLI, so `actions/checkout` fails before
  anything reaches the daemon. It now describes what actually works — a container label on
  an image that already carries the tools, `docker_host: automount`, and a *second* runner
  scoped to one repository, because `container.docker_host` is per runner instance and
  cannot be scoped to a label. (#190)

### 🔧 Changed

- **The support link is a QR code now, because a button can only be pressed by whoever is
  already holding the device.** A README is read on a laptop, shown over a shoulder,
  projected in a talk, pasted into a screenshot. In every one of those the *Buy me a coffee*
  button was visible and unusable. The code is the same link in a form a second device can
  pick up, and it replaces the button in the README, in `FUNDING.md` and on the wiki's home
  page. The wiki sidebar keeps its text link: a sidebar has no room for a code.

  **It was read back rather than trusted.** The code has little margin — stylised round
  modules with the cup over the centre — and scaling it is not a matter of picking a round
  number: a strict decoder reads the 3000px original at 300, 330, 375 and 440 pixels and
  fails at 360, 400, 500 and every size above. 330 is shipped because it decoded under every
  resampling tried, and `assets/support/NOTICE.txt` records that, along with what was done
  to the file and what it resolves to. A phone is far more forgiving than a strict decoder;
  anything that regenerates or resizes this file should still read it back first.

  `TRADEMARKS.md` covers the code as well as the banner now. The banner file stays in the
  tree, unshown, because links to it exist outside this repository. Nothing about the
  promise changes: this is still the only place Postulo asks, and **nothing inside the
  application ever will**. (#172)

- **A plugin's kind and where it came from no longer wear the same badge, and the kind is a
  word in your language.** Both pages that list plugins drew two different facts as
  identical grey pills side by side, so nothing said that *importer* and *official* answer
  different questions — and the kind was the raw slug, so a French reader was shown "source"
  and "feature" in English. The person's own *Settings → Plugins* did not show where a plugin
  came from at all, which is the page where it matters most.

  The kind now takes colour, because it is a category from a small fixed set and colour is
  what makes eight of them scannable in a list. **Where it came from stays grey**, and that
  is not a default: `server/plugins.html` has said since #94 that provenance is *"deliberately
  not styled as a reassurance — installing a plugin runs somebody else's code whatever this
  says"*, and a green *Official* badge would undo that sentence. Both pages now draw both
  tags through one partial, so they cannot drift apart again, and the origin appears on the
  settings page for the first time.

  Colour is never the only carrier: every tag says its own word, each tone reaches 4.5:1
  against its own ground in both themes, and the palette repeats across the eight kinds on
  purpose — eight hues told apart at a glance is more than a palette honestly gives, so kinds
  rarely seen together share one and lean on the word. A kind this version does not know
  shows its slug in the neutral tone rather than nothing, because a third-party plugin may
  declare one added after this release. (#184)

### 🐛 Fixed

- **A CV's page no longer says “What is on this cv”.** The heading put the kind's label,
  lowercased, into a sentence — which flattened an acronym in English, French and
  Portuguese and misspelt a noun in German, and which could never agree in a language where
  the two kinds take different articles. It is two sentences now, chosen by kind: *What is
  on this CV* and *What is on this portfolio*. The same `|lower` was applied to a salary's
  period (*Pro Jahr* → *pro jahr*) and to a copy's status after a store's name; both now
  show the label as the catalogue wrote it. Filled in English, French and Portuguese; the
  other catalogues get the two new strings at the release sweep. (#168)

- **The suggestions page fits on a phone once there is a suggestion on it.** The accept form
  for a suggestion not yet matched to an application carries a select as wide as the longest
  application title, in a group that could not shrink — 45 pixels past the edge of a
  320-pixel screen in English, 60 in Greek, 70 in German, on exactly the page a mail or
  calendar plugin's first suggestion lands on. The group wraps under the words now and the
  select gives way. The walk had never reached that state: its fixture had no suggestion, so
  the page was only ever checked empty. It has a pending, unmatched one now, filed the way a
  plugin files one, so axe and the reflow check read the page with something on it — the
  last of what #167 found and left undone. (#167)

- **Escape closes a cell editor however quickly it is pressed.** The editor arrives by a
  swap, and htmx wires what it swapped in — the Cancel button Escape clicks — only when the
  swap settles, 20 ms later by default, while the caret is put in the input the moment it
  lands. For those 20 ms Escape reached a button nothing was listening to, and did nothing.
  No hand is that quick; the browser suite was, one run in three, and the flake it reported
  was this. The handler now has htmx process the editor before clicking, a no-op once the
  settle has done it, and a test widens the window to two seconds and presses inside it.
  (#161)

- **The image scan can now be told from its own failure.** `scripts/scan-image.sh` has three
  outcomes instead of one red step: exit 0 for nothing fixable, 1 for fixable findings, and
  2 when the scan did not complete — a scanner that failed to run, a bill of materials that
  came back empty — which says nothing about the image and now says so, on stderr and in
  `.scan/verdict.txt`, which both image workflows put at the top of their run summary. Trivy
  runs once for the gate rather than twice, with an exit code of its own for findings; Grype
  exits 1 for everything, so its verdict is read from whether a report came out. A verdict
  left by an earlier run is removed before anything starts, and the dev image's summary no
  longer offers a `docker pull` for an image that was never pushed. The root cause — reports
  written through a bind mount the daemon resolved against the host — went with #190, and a
  test now keeps every report on a redirect. `.scan/` is ignored, so a scan run by hand
  leaves no untracked files behind. (#192)

- **An uploaded file downloads under its own extension.** Every download was called
  `<title>.pdf`, whatever had been uploaded, so a `.docx` or a `.txt` arrived as a file no
  PDF viewer would open — served as `application/pdf` too, since the type is guessed from
  the name. The name is the title with the upload's own extension now, on the page and over
  the API alike; a snapshot of what was sent is still `<title>.pdf`, because that is always
  what it is. (#193)

- **A file's edit form no longer shows where Postulo keeps it.** Editing an uploaded file
  said *Currently: documents/1/2026/09/reference.txt* — the storage path, carrying the
  account id and the month of the upload, inside a link to `/media/` that nothing serves. It
  says *Currently: reference.txt* now. The path was Postulo's filing system thinking aloud:
  not the person's to care about, a second answer beside the *Title* field above it, and on
  a shared screen an account id. Django's file widget template is overridden once, for every
  file field, so no form has to remember. (#191)

- **The accessibility walk claimed thirty-five pages it never opened, and six of the pages
  it had never opened were broken.** `tests/test_page_coverage.py` insisted every URL
  pattern was either visited by the browser suite or excused in writing — but "visited"
  meant "named in a hand-written tuple beside the walk", and nothing held the tuple to the
  walk. It claimed 104 names and the walk reached 69. Every CV page and every letter page
  past the list was counted as checked and had never been looked at.

  **The claim is derived from the walk now**, by resolving the paths it actually visits, so
  it cannot get ahead of it again. That turned the coverage test's output into an honest
  list of thirty-four, and the walk was given what those pages need to exist: a CV with an
  entry, a letter, an upload, a tag, a contact, an industry, a posting, a capture waiting
  for review and a connection. Two routes answer POST only and are excused by name rather
  than pretended at; `settings:index` stopped being excused as *"redirects to the appearance
  page"*, because the walk visits `/settings/` itself and the excuse had become false.

  **What the fuller walk then found**, none of which anything had ever measured:

  - A CV's own page ran off a 320-pixel screen in Greek and German. Its entries are the row
    #165 fixed on the career page, and this page had kept the old one: words beside a
    `shrink-0` group of ↑, ↓, *Tailor* and *Remove*. The grid's column is sized by its
    content, so the overflow took the *Add entries* card with it.
  - The uploads list squeezed a title into 58 pixels in English and 13 in Greek, behind
    *Download / Edit / Delete*.
  - The connections list did the same, with `flex-1` — which is `flex: 1 1 0%`, so the words
    claimed no width of their own before anything wrapped.
  - The upload **edit** form scrolled sideways in every language, by exactly the same amount,
    because Django renders a bound file field as `Currently: documents/1/2026/09/reference.txt`
    — a path, with no spaces in it, 252 unbreakable pixels against the 238 a phone leaves.
    The *new* form passed all along, which is why nothing had caught it.
  - A checkbox on the applications filter was 20 pixels tall, against the 24 WCAG 2.2
    SC 2.5.8 asks for.
  - The capture review page pointed `aria-describedby` at a help-text element that was not
    there — worse than no description, because a screen reader is told there is one.

  All six are fixed. The preview pages are the one exemption, in writing and narrowly: a
  preview returns *"the CV as HTML, exactly as the PDF renderer will see it"*, so axe is
  reading a print document and asking it for `<main>`. Satisfying that would change every
  PDF Postulo produces to answer a question nobody asks of a printed page. They stay in the
  walk, so reflow and target size still read them; only axe looks away. (#167)

- **Dragging worked in Chromium and did nothing in Firefox, and the tests agreed with
  Chromium.** Neither drag cancelled `dragenter`. The specification makes an element a drop
  target only once *both* `dragenter` and `dragover` are cancelled; Chromium forgives the
  omission and Firefox does not, so arranging the dashboard by dragging a widget (#125) and
  moving a card between board columns both sprang back with nothing posted and nothing
  logged, for anybody using Firefox.

  **Both drags had it**, because both were written the same way — the board's cards and the
  dashboard's rows — so both are fixed here rather than one being left with a known copy of
  the fault in the same file.

  The tests could not have caught it. They dispatch four synthetic `DragEvent`s including
  the drop itself, so they exercise the handlers and pass whether or not a browser would
  ever have delivered that drop; and the browser suite runs `--browser chromium`, the one
  browser that forgives this. Both helpers now send `dragenter` as a browser would, and each
  file gains a test that asks the handlers the question the browser asks — *was this event
  cancelled?* — which fails without the fix in Chromium, so it needs no second browser to
  keep watch. (#174)

- **A chip with no remove button had almost no padding at its end.** On *Documents → CVs*
  the kind tag sat hard against its own right edge, twelve pixels of space on one side and
  two on the other. `.chip` was shaped around a button it did not have: the two pixels are
  where `.chip-remove` goes, and the button supplies the visual space, so a chip holding only
  a word got the gap and nothing to fill it. Two of the three places that draw one have no
  button — a CV's kind and a company's industries — and the company row had already patched
  it by hand with `pe-3`, which is the sort of workaround that says the class is wrong rather
  than the caller.

  So the class changed rather than the third caller: a chip pads both ends, and the remove
  button pulls its own end back with `-me-2.5`. A chip with a button looks exactly as it did;
  one without is no longer short of an end; and the fourth caller will be right without
  knowing any of this. The company row's `pe-3` is gone.

  The test that guarded this pinned `ps-3` and `pe-0.5`, which is to say it pinned the
  asymmetry rather than the intent — its own docstring says the intent is that the × sits at
  the end edge and not the right. It now asserts that, and that a chip pads both ends. (#185)

### 🔧 Changed

- **Grids use the whole screen; everything else keeps its measure.** Every page sat in a
  1280-pixel column — `max-w-7xl` on `<main>` — so on a wide monitor a table with ten chosen
  columns scrolled inside a box with grey on both sides, two nested scrolls to read one row.
  `<main>` takes its width from the page now: the tables, the board, the dashboard and the
  lists empty the cap and take the screen; forms, detail pages and prose keep the measure
  they had, because a wide page is not a wide paragraph. The one criterion about line length,
  SC 1.4.8, is level AAA, and its eighty-character measure is what the forms' `max-w-2xl`
  already is. The header and footer span the width, so a masthead never sits narrower than
  the table under it. Below 1280 pixels nothing changes. (#188)

- **Server overview: the card at the bottom has lost its heading.** It said *Also*, which
  names nothing — the other headings on that page are *Overview*, *Software* and *Data*, each
  saying what is under it, and this one said only that there was more, which the reader can
  already see. It is the heading somebody writes when a card has collected what did not fit
  elsewhere, and that is what the card is: a link to the health check, and a link to Django's
  admin or a note saying it is off. Both say what they are and where they go, so the card
  carries itself. (#183)

- **Twenty-four languages are no longer translated before anybody has read the English
  once.** `test_every_european_union_language_stays_complete` held the twenty-four European
  Union catalogues at 100% on every commit, which made adding one user-facing string cost
  twenty-four translations — made by the same hand that wrote the English a minute earlier,
  on wording that had not settled, for no reader. The promise was always about a *published
  version*: what somebody installs should not have a gap in a language Postulo offers them.
  It was never about a branch halfway through a feature.

  There is now a `release` marker — *a promise that must hold in a published version, not in
  every commit* — deselected by default exactly as `e2e` already is, and
  `uv run pytest -m release` is **step 1 of *Making a release***, before the changelog is
  moved or anything is tagged. The same twenty-four must be complete, checked by the same
  assertions; the check is simply first and deliberate rather than implicit in a suite that
  had already run. Ordinary work translates English, French and European Portuguese, which is
  enough to see a change in three languages and catch what only shows up in one — the long
  German string that broke a row in #165 was found that way.

  Nothing else is marked. Plural rules, placeholder consistency, catalogue currency, the
  "a started catalogue actually translates" gate and the render-in-every-language walk still
  run on every commit, because those catch mistakes rather than measure completeness. The
  risk is stated plainly in the issue: somebody can now commit a string only three languages
  have, and that is only safe while the release step stays a hard gate. (#182)

### ✨ Added

- **The sources read the posting the page is showing, in whichever way the board wrote it.**
  Four faults, all found by reading real adverts rather than hand-written objects, and none
  of them catchable by any test that existed. A results page carries a `JobPosting` for every
  hit and the first was taken, so a capture from one brought back whichever advert the board
  listed first; the one naming the page's own address now wins. A posting hung off an
  `ItemList`, which is how several large aggregators publish, was invisible — only `@graph`
  was followed. **Microdata and RDFa are read**, so the older recruitment systems and
  public-sector boards that publish `itemprop` markup and no script stop falling through to
  the fallback and coming back with the page's `<title>` as the job title; it is the same
  schema.org vocabulary in the spellings the standard also defines, so it is the same source
  reading it rather than a second one. And fields were read from one place where boards write
  them in two: the office named on `hiringOrganization.address` rather than `jobLocation` was
  lost outright, `estimatedSalary` was never read, `applicantLocationRequirements` — which
  schema.org defines as where an applicant may be *for a job done remotely* — never counted
  as remote, and a currency was kept with no amount behind it, so captures arrived saying
  "USD, per year" and nothing else because boards write a `0–0` placeholder into every advert.

  **For the boards that publish nothing a standard can read, a recipe.** LinkedIn embeds no
  JSON-LD and no microdata and marks the company and the place with class names only, and
  Greenhouse — which hosts a very large share of what people apply through — publishes
  nothing either. `plugins/builtin/boards/` holds one module per board behind a source that
  runs first and lets the standards fill whatever it left empty, so a board that starts
  publishing JSON-LD improves without its recipe being touched and a recipe that rots because
  a board redesigned costs the fields it used to fill rather than the capture. A recipe states
  only what it is sure of: LinkedIn shows its date as "2 months ago", relative and in the
  reader's language, so no date is stated at all. Its employment type is read the way this
  sort of thing should be — every value in the criteria list is offered to Postulo's own
  vocabulary and the one it recognises wins, so "Full-time" is read, "Mid-Senior level" is
  not, and a page in a language whose words Postulo does not know leaves the field for a
  person rather than guessing.

  Two fixes came out of the same page and help every board. Where a page gives **exactly one
  `<h1>`** outside its furniture, that is a better job title than the one it declares for
  sharing — LinkedIn's is "*company* hiring *job* in *place* | LinkedIn". And **link density**
  reaches what no landmark rule can: "similar searches" and "people also viewed" are plain
  `<section>`s marked with a class name and nothing else, and a block whose text is
  overwhelmingly inside a lot of links is a list of links whatever tag it uses.

  All of it is held to **whole pages now, not hand-written objects**. `tests/fixtures/postings/`
  carries eight, each with the expected reading beside it and a note saying what that board
  does differently; every quirk in them was taken from a live page. **The browser extension
  carries the same eight** and is held to the same values, because a page read in somebody's
  browser has to come out identical to that page read here when it is sent — and every field
  of all eight was checked between the two, and against live LinkedIn, Greenhouse and We Work
  Remotely pages. To make that comparable, `htmlutil` assembles the tag stream into a small
  tree before walking it: still the standard library's parser, still no C extension, but
  microdata nesting and "is this paragraph inside the navigation" are questions about nesting
  that a stream cannot answer without guessing differently from the DOM the extension walks.
  (#176)

- **A capture can be looked at before it is sent, and corrected on the way.** The browser
  extension used to send a page and hope: `POST /captures` read and stored it in one go, so
  the first anybody saw of what the parser made of it was the review screen, a tab and a
  sign-in away. `POST /captures/preview` answers with what a page would be captured as, and
  which source read it, and stores nothing and tells nobody; the extension shows that in its
  popup. `POST /captures` takes the fields the person changed as `data`: the page is still
  read, so the capture's source stays the one that read it, and each field given replaces
  what was read before the whole is checked exactly as a source's own output is. An unknown
  field or an empty title is a 422.

  **What a `captures` token may do has not moved.** A corrected capture is still pending,
  and the review screen opens with the corrections filled in: corrected is not reviewed, and
  a listing still exists only once somebody has saved it there. (#171)

- **A portfolio is a document Postulo makes, and what kinds of document exist is said in one
  place.** The first of the three stages the issue itself proposes — *portfolios need the
  polymorphic link and the theme rule and nothing else* — and both of those shipped already:
  a render points at whatever made it and a copy at whatever it copied (#130), and a theme
  declares which kinds it can set (#132).

  **A portfolio is a CV with a different shape**, not a second model. It *is* a selection
  from the career record with its own layout, and it uses every line of the same machinery —
  the same entries, the same tailoring, the same *included* switch. Two near-identical models
  would have been two forms, two lists, two exporters and two of every future change. The
  precedent was already here: a letter has carried four shapes told apart by a field since
  it arrived. And *portfolios of different kinds* — a developer's, a designer's, a
  researcher's — is answered by the theme and by what is selected, which are the two things
  they actually differ in and are already the person's to choose.

  **The kind vocabulary was doing two jobs and now derives from one.** It labelled an
  uploaded file *and* a rendered one, with a letter mapping its own shapes onto it by hand —
  two vocabularies with no relation, waiting to disagree. Everything said *about* a kind now
  comes from one registry, and the pickers read it through a callable, so a kind a plugin
  registers reaches every menu and every store's per-kind switch without a migration. That
  is what makes "a kind is a plugin" something other than a phrase.

  **And a store stopped asking what it was holding.** The last two places that checked
  render-or-upload now ask the document itself, so a third thing that holds a file needs no
  branch — proven by a test that describes a class the store code has never heard of.

  Reports and emails are deliberately not here; #162 records what each still needs settled
  and why guessing at it now would be designing against nothing. (#133)

- **An employer can be a structure, and an application can say which part of it the attempt
  was for.** The last of three: a company inside a company shipped with #55, a department
  inside a company with #137, and this is the attachment, the switch, and the question a
  tree makes every count answer.

  **"Attached to any of the four" turns out to be three fields, not four.** A posting's
  company is the company applied to, whatever height of the tree it sits at, so *parent
  company* and *child company* are the same column holding different rows; a contact was
  already a direct optional link. Only the department was missing.

  **The shape it was given is the decision the issue turned on**, and the plugin toggle
  settled it. Naming a department *enriches* the posting's company rather than replacing it,
  so that column stays required and every application has exactly one employer however this
  is set. Had the employer link itself been made polymorphic — a posting pointing at a
  company, a department or a person — then switching the feature off would leave applications
  attached to departments with no company to fall back to, which is a toggle that breaks a
  page rather than a toggle. A department anywhere in the employer's *group* may be named,
  because an application through the Irish arm can be for the group's engineering team;
  anywhere else is refused in a sentence naming the employer it is actually at.

  **Every count now says which reading it is showing.** A company page counts that company,
  exactly as it always did, and offers *across the group* beside the heading where there is a
  group — in the address, so a page counting a whole group can be bookmarked and sent.
  Changing what a number means without being asked would be the other half of the same
  mistake a hierarchy is meant to fix, so the default did not move. The companies table gains
  a **Part of** column whose name narrows the table to that whole ownership tree,
  grandchildren included.

  All of it is one feature plugin, **Employers with a structure**, on by default so an
  upgrade takes nothing away. Off is exactly what Postulo did before any of this existed —
  one company per posting and nothing else — and deletes nothing: the parents, the
  departments and the attachments stay recorded and come back untouched. (#138)

- **A report about a period, to hand to an employment office or to read yourself.** Two
  different people ask for this and they want the same document. Unemployment benefit in most
  of Europe is conditional on actually looking, evidenced — and until now the only way to hand
  those facts over was to copy them out by hand, which is exactly the work that makes people
  stop keeping records at all. The other reader is the person searching: a month of looking
  feels like nothing happened, and *eleven applications across four weeks, none in the week of
  the 12th* is the difference between a feeling and a fact.

  **Regularity is what it is named for, so the cadence is the top of the page** — a bar per
  week, the empty weeks saying *none* in words rather than being an invisible gap, the average
  per week, the longest gap and the run of weeks up to now. Underneath is the evidence: every
  application sent in the period, with where it was found, the address of the posting, its
  status and the date of the last thing that happened.

  **Two questions, kept apart rather than added together.** *Sent in this period* reads
  applications by when they went out. *What came back* reads the event log by when the event
  happened, so a reply arriving in September to an August application is September activity —
  which it is. Folding the two together would give a figure that is true of neither.

  **The period is in the address**, which makes a report for a particular month a thing to
  bookmark and to send to somebody: the same line this project already draws between a
  question and a preference. There is no link into the future, because a report about next
  month is a blank page pretending to be a document.

  Out as a **PDF** carrying the person's name, the period and the day it was produced —
  because a document with no date is not evidence of anything — and as **CSV** for anybody who
  wants to do their own sums. Nothing is stored: a report is computed when it is asked for,
  from records that are already the truth, which is what makes it a snapshot of the record at
  a moment.

  **Postulo sets no target anywhere on the page**, and says so on it. How many applications a
  period should hold is a benefit regime's rule or the person's own, never this software's.
  Drafts never sent are left out, and the page says how many, so the absence is stated rather
  than silent. (#56)


- **An address is now checked — and named — by the rules of its own country.** Which parts
  are usually needed, what a postcode there looks like, what each field is *called*, and what
  order the whole thing prints in.

  **Naming the fields turned out to be the harder half, and it is not a translation problem.**
  Somebody reading Postulo in Portuguese who enters a United States address should see
  *Estado*; entering a Portuguese one, *Distrito*; a Japanese one, *Prefeitura*. The label
  depends on the **address's** country and the language depends on the **reader**, and both
  vary at once — nothing else here has that shape, because every other string is chosen by
  the reader's language alone. So the table names a key and the catalogue supplies the word.

  **Nothing refuses.** Every rule is a note. BFPO addresses, rural routes, informal
  settlements, temporary accommodation, and a table that is simply wrong about somewhere: an
  address that fits no rule is saved exactly as typed, because an application that will not
  accept your address is telling you something about who it was written for. Ireland expects
  no Eircode, because Eircode arrived in 2015 and plenty of addresses predate it.

  **The rules are a curated table, and what was ruled out is written down.** Google's
  `libaddressinput` is the set everybody reaches for, and a project that exists as an
  alternative to services answering to somebody else's jurisdiction does not put that
  jurisdiction in its address form — recorded in the module so nobody proposes it again. What
  is left is the shape this codebase already uses for the country list, the language names and
  the plural rules: rows decided rather than derived, with the reasoning per entry, no
  third-party licence and no update pipeline. It records when it was last reviewed.

  A country with no row gets no rules — free-form entry, neutral labels, and the parts printed
  in the order they were entered — which is also exactly what switching the feature off does.
  Off is the same code path rather than a second one nobody has seen. (#147)

- **Postal addresses have somewhere to go.** Several per account, exactly one primary, on a
  person and on a contact — with the invariant in a database constraint rather than in
  whichever form saved last. `Profile.location` keeps working and keeps its meaning; this is
  for the parts of an address that had nowhere to be.

  **An address is not unique across the instance, and that is the point rather than an
  omission.** A telephone number belongs to one person; a home does not. Spouses share one,
  flatmates share one, an adult child at home shares one, and two siblings on a family
  instance share one — and a family instance is exactly the kind of small self-hosted
  deployment this project is built for. A uniqueness constraint would refuse the second
  member of a household their own address *and* disclose, in refusing it, that somebody else
  on this server lives there. So: unique per owner, so nobody lists their own home twice,
  and freely shared between accounts. The page says so, because the numbers beside it make
  the opposite trade.

  **Valid cannot mean verified.** Deciding whether an address exists needs a per-country
  reference database or a paid lookup — a network dependency, a cost and a stream of updates
  — and Postulo has no use for the answer, because it is not going to post anything. An
  address typed oddly is saved exactly as typed; only the comparison folds case and spacing.
  Nothing here has a verification and nothing here can become a way back into an account.

  **Postulo was throwing real addresses away, and has stopped.** A Europass file carries a
  street and a postcode; the importer kept the town and the country and discarded the two
  lines that make an address an address. Somebody exported their CV, imported it here, and
  they were silently gone. They land now — filling blanks only, never arguing with an
  address somebody typed.

  The archive carries addresses (format 9), an import brings them back, and two people can
  import the same one. The CV header still shows a town and a country and never a street:
  guidance across most of Europe is that a precise address invites a reader to draw
  conclusions about somebody from where they live, and putting one on a document sent to
  strangers is not a default anybody chose. `docs/THREAT-MODEL.md` now says a home address is
  the sharpest thing this application holds. (#92)

- **Several email addresses is a feature you can switch off — and it governs the page, not
  the addresses.** Everything the request asked for already worked: allauth gives an account
  several addresses, exactly one primary, each verified independently, and that is the shape
  telephone numbers were built to copy. What was missing was the framing, and framing it
  raised a better question than it answered.

  **A feature may govern a page it does not own the data behind.** The model belongs to a
  library, with its migrations behind it and its own flows reading it — verification,
  password reset, signing in by address, linking a social account — and a plugin that cannot
  verify an address cannot honestly own one. So *off* means Postulo offers the primary
  address and stops offering the management page, and it deletes nothing because it owns
  nothing. That is the same thing `phone-numbers` does: a feature governs what Postulo offers
  and uses, and neither of them destroys anything to do it.

  **What off could have cost is the reason there is a floor under it.** Somebody keeps a
  second address because the first is a work account they are about to lose; hiding the page
  does not remove that address, but it removes their ability to promote it on the day they
  need to — a lock-out arriving through a setting nobody thought was about getting back in.
  So the page is never withheld from an account that already has more than one address, or
  from one whose only address is unverified, whatever the policy says.

  Worth saying plainly: with those floors, the visible change is a link disappearing. (#145)

- **Mail has two halves now: the instance's and yours.** The instance's was already there —
  the SMTP transport, ungoverned on purpose, configured under *Server settings → Email*, and
  refusing to be switched off while it is the last way anybody could get back in. The other
  half is *Sending as yourself*: your own server, your own address, switchable by you or by
  an administrator, under *Settings → Connections*.

  **It is a new kind of plugin rather than a second transport, and that is the whole point.**
  Getting back into an account reads transports. An outbox is not one, so a person switching
  their own mail off cannot thereby remove their own way back in — the failure that made
  transports ungoverned in the first place, arriving through a door nobody had locked. By
  construction rather than by anybody remembering, and asserted.

  **Sending as somebody is the half that needs settings of its own.** Putting your address on
  a message that leaves the instance's server is spoofing: SPF says that server is not
  authorised for your domain and a receiving server bounces it or bins it. So it goes over
  your server with your address on it — your domain, your reputation, and replies and bounces
  coming back to you, which is right. Postulo never rewrites the sender: a message whose
  `From` was quietly replaced is a message that looks forged, so a mismatch is refused
  instead. The same destination guard as the instance's own mail, and a per-account limit,
  because this is the one surface where a mistake reaches strangers.

  Switching it off means *you cannot send from your own address here* — not that you cannot
  be notified. Everything the instance sends on its own account is untouched. (#149)

- **Server settings → Plugins now lists what the instance can actually do, and says where
  each part of it came from.** It used to list what an administrator had installed — so the
  two built-in capture sources, which are classes in the image and never pass through the
  installer, were missing from the page somebody would look at to answer "can this instance
  read a posting off a page". They are there now, marked internal, with no button that could
  not work: a built-in has no line in the record for Remove or Switch off to act on, and is
  governed for people through the policy rows above instead.

  **The hard half is labelling an upload, and it comes down to one sentence: a zip is a zip.**
  A file somebody uploads carries no evidence of who published it, and calling it official
  because it is named after an official plugin would be worse than not labelling it at all.
  So the label describes evidence: a signed index publishes each release's SHA-256, and a
  wheel whose bytes match one is byte-for-byte the file that repository signed, however it
  reached the instance. One byte of difference and it is not. When no repository can be
  reached — switched off, unreachable, an index that will not verify — the answer is
  *uploaded* rather than a guess.

  **Which repository counts as official is decided by a key, never by a name**, because
  anybody can call their repository `postulo` and nobody else can sign with Postulo's key.
  Postulo publishes no catalogue yet, so nothing is official today and every installed plugin
  is custom or uploaded — which is the truthful thing for the page to say, and it changes by
  adding a key rather than by writing this again.

  **The badge does not quietly mean "safe".** Installing a plugin runs somebody else's code
  inside Postulo, and that is as true of an official one; the page still says so. Nor does it
  cover dependencies: the signature and the checksum are about the plugin's own wheel, and its
  requirements come from PyPI at install time. Neither sentence beside a badge says otherwise,
  and a test holds them to it. (#94)

- **A plugin can carry a logo, and Postulo serves it.** The manifest has had a `logo` field
  since #97 with nothing rendering it. Now a plugin names a file inside its own package and
  it appears beside the plugin's name.

  Three constraints shaped it and all three had been settled once already. It cannot be a
  static file: plugins are installed at run time, `collectstatic` ran when the image was
  built, and the manifest storage raises on a file it never learned rather than returning a
  dead link. It cannot be a URL: the production policy is `img-src 'self'`, and an image at
  the plugin author's server would tell them which instances run their code, how many people
  use it, and when. And it is raster only, because SVG can carry scripts and a direct visit
  to the file is not the `<img>` context where a browser refuses to run them — what is
  served is a PNG Postulo produced from what the plugin shipped, not the plugin's file passed
  through.

  **Having no logo is the normal case, not a failure.** The interface falls back to the
  initials tile it already uses for a person with no picture and a company with no logo, and
  a declared file that is missing, oversized or unreadable falls back the same way with a
  line in the log. Nothing is ever a broken image.

  **Postulo ships no logo for any plugin of its own, and that is the decision rather than
  the backlog.** Displaying somebody's mark to say "this reads Europass files" is nominative
  use; *redistributing the file* under AGPL-3.0 would be sublicensing a mark the project does
  not own, and the Commission's reuse decision explicitly excludes logos from its scope. The
  rule is written in `docs/PLUGINS.md` rather than decided logo by logo, because one that
  only works for the marks a project happens to like is not a rule. (#106)

- **A plugin Postulo ships can hold its own translations now, and holding them costs it
  none of the coverage that keeps them translated.** `docs/PLUGINS.md` has always said a
  plugin's strings are never added to Postulo's catalogues. That was true of every
  third-party plugin and false of every plugin Postulo ships, because there was one
  catalogue in the repository and everything was in it.

  **The guarantee was the whole difficulty.** One test says the twenty-four European Union
  languages stay complete, and it walked one directory. Move a built-in's strings out and
  they leave its sight — and for a plugin Postulo ships, Postulo is the author, so the
  outcome is not "translated by somebody else", it is "quietly untranslated". So the
  tooling learned about several sets of catalogues instead: `extract` writes each string to
  the set that owns the file it came from, `check`, `stats` and `compile` walk all of them,
  and every promise the tests made about the catalogues is now made about each set. Sets
  are found on the filesystem rather than listed anywhere, so a plugin that moves its
  strings is covered from the moment the directory exists.

  **Which catalogue wins is now a decision rather than an accident.** Two catalogues can
  translate the same English word, and Django resolves that by the order of the locale
  paths — first one wins, and a plugin's is appended. So Postulo's own rendering is always
  the one shown: a plugin can add a word to the interface and cannot change one. Asserted,
  because it was previously true by luck.

  The two built-in capture sources moved first, with their thirty-nine translations
  unchanged and the HTML helper they were the only user of. They were also the plugins the
  surface test called wholly independent — which turned out to be an artefact of the test
  reading only absolute imports; resolving the relative ones as well found three more real
  dependencies that had been hiding behind a dot, each now written down as work #129 has
  left to do. (#127)

- **A written, enforced answer to "a plugin should not depend on the core": depend on
  `postulo.plugins.api`, and nothing else.** The imperative could not be checked — or even
  argued about — while there was no answer to *depend on what, then*. Now there is one module
  that is a promise, everything else is this month's internals, and a test walks every plugin
  Postulo ships and fails on one that reaches past it.

  **Total independence was never the goal.** A plugin holding one person's data must scope it
  with `for_user()` or one person sees another's; a redirect that skips `safe_next` bounces
  somebody off the instance; an outbound request that skips the guarded client dials where the
  server should not; a consenting connection that keeps a token has stopped refreshing it.
  Those four are *reasons* to depend on Postulo, and the imperative is served by making them a
  small named set rather than by pretending they are avoidable.

  **The promise about breakage is made now, deliberately.** Having no surface was free, and it
  stops being free the moment #129 moves shipped plugins into their own packages — a package
  outside `src/postulo` has to know what it may import, and deferring the promise means either
  deferring that work or setting a boundary by accident. From here a change to any of these
  names is a deprecation entry first and a removal later, never a silent rename.

  **Enforcement is what makes it real**, and it reads the source rather than importing it: a
  lazy `from postulo.core import site` inside a method is exactly as much of a dependency as
  one at the top of a file, and is the shape most of the remaining ones take. The plugins that
  still reach past the surface are recorded with what each needs — that list is the map of what
  #129 has left to move, a new entry has to be written on purpose, and a stale one fails too,
  because an entry nobody removed hides the next real dependency.

  The two built-in sources import nothing from Postulo at all and the telephone-numbers feature
  imports only the surface, which is the check that the surface is not so wide as to mean
  nothing. (#126)

- **A connection can say it needs consent rather than a password.** Every connection until
  now authenticated with something a person could type — a field, a form, a stored secret, a
  *Test* button — and OAuth is not that. It is a round trip through somebody else's website,
  two HTTP requests, a browser redirect and a token that has to be renewed before every use,
  none of which is a field. `Consent` sits beside `FieldSpec` as part of the vocabulary a
  plugin uses to say what it needs, and Postulo conducts the round trip.

  **Tokens live in the connection's own encrypted secrets, not in allauth's.** Reusing the
  sign-in grant would have saved a great deal and been wrong twice over: it carries the scopes
  asked for at sign-in, and asking for a mail scope at sign-in *so it might be useful later* is
  exactly the over-broad consent this project should not be teaching — and allauth holds one
  token per account per application, so a second grant has nowhere to sit beside the first. Two
  grants, asked for when each is needed, in one store under one key.

  **The callback address is shown, not described.** OAuth needs a redirect URI registered in
  advance, and it is this instance's own public address — something a self-hosted application
  behind a proxy may not know about itself, and which changes when somebody moves it. Getting
  it wrong ends a consent screen in an error nobody can read, so the connection's page prints
  the exact string. One address for the whole instance rather than one per plugin: registering
  it by hand once is the difference between usable and a chore repeated per provider.

  **The scopes are on the page too**, in the provider's own words, because a list nobody can
  read is a list nobody consented to.

  **"Consent was withdrawn" is a distinct failure**, which is what the *Test* button now means
  for a connection like this: it proves the grant still stands before it proves anything else.
  Nothing is misconfigured, nobody typed anything wrong, and the fix is to agree again — none
  of which a mail server can tell you. A provider's other refusals are kept apart from it.

  **The refresh goes through the guarded client**, because it is an outbound request in the
  middle of delivering somebody's mail and deserves the destination policy, timeout and
  redirect limit every other outbound request gets. And the state carried through the round
  trip is signed, short-lived and checked against whoever is signed in when it comes back: a
  callback is a request another page can cause.

  `docs/THREAT-MODEL.md` gains the line the issue asked for — a refresh token outlives a
  password and is not changed by changing one, and only the provider can revoke the grant.
  `CONTRIBUTING.md` has the shape for plugin authors, including why not to reach for the
  sign-in tokens. (#150)

- **Tables can act on several rows at once** — tag applications, move them to a status, put
  companies in a field of activity. Four decisions were forced by the shape of the feature and
  each is taken in the code rather than left to a template.

  **Owner scoping moves, and that is the whole risk.** Every view until now fetched *one*
  object where a foreign id is a 404; a bulk action receives a list of ids from a browser,
  which is exactly the shape of request that goes wrong. Every id is re-scoped and the action
  works on the intersection — never on the list that was sent, and never by refusing the whole
  batch, because refusing is an answer and an answer tells the sender which ids exist. Forty
  ids of which thirty-nine are somebody else's changes the one and says "1 changed". Sixteen
  tests in `tests/security/` hold that, including that the message never reports how many were
  *sent*: the difference between sent and changed is a count of somebody else's rows.

  **Additive only.** Deleting forty things is a different act from deleting one, and for a
  company it cascades to every posting under it — which the interface says plainly for one
  company and would be saying about an unseen number for forty. That confirmation deserves its
  own design; until it has one there is no bulk delete.

  **A changed query clears the selection.** Tick twelve, narrow to four, press the button —
  every answer surprises somebody, and acting on twelve while showing four is the surprise with
  consequences. The checkboxes are form state on a page and a filter change loads a fresh one,
  so the rule is also what happens naturally and cannot drift out of true.

  **Select-all means this page, and it is a button rather than a header checkbox.** A checkbox
  in a header row is inert without script, and an inert control is worse than a missing one; the
  script *adds* a button saying what it does. "All 340 matching" is not offered — it is a
  different promise that has to work from the query rather than a list of ids.

  It is a form before it is anything else: checkboxes sharing a name and a submit button are a
  complete implementation, and everything scripted sits on top. The count is spoken through a
  live region, because a selection that exists only as a column of ticks is not a selection.
  Status changes go through the event log rather than `update()`, so forty applications moved
  are forty applications that can say when they moved. (#134)

- **A rule for what happens to a plugin's data when the plugin goes, written before any
  plugin owns data.** That timing is the point. Everything the newest plugin governs lives in
  core, so the question has never had to be answered — and the moment one is made
  self-contained it becomes a Django app with a migration history of its own, and three
  questions arrive at once with no answers.

  > A plugin that owns a table may not be uninstalled while that table holds anything.

  Postulo refuses, names how many records are in the way, and leaves both the package and the
  data alone. **Two other answers were open and neither is safe.** *Keep the table* leaves data
  nothing can read, export or restore — present in every backup, absent from the export of the
  person whose data it is, invisible to `migrate`; that is the failure the export exists to
  prevent. *Delete it behind a confirmation* makes removing a package a data-destroying act,
  when somebody may only be swapping it for a newer build of the same plugin — and Postulo
  promises in thirty-nine languages that switching a plugin **off** deletes nothing, so putting
  *uninstall* on the other side of that promise is a distinction nobody holds in their head at
  the moment it matters.

  Refusing is also the shape this codebase already uses three times over: the last
  administrator, the mail transport that is the last way back in, the plugin that is somebody's
  only recovery route. A refusal naming what holds it is something a person can act on.

  **Off and uninstalled are now different acts**, and the difference finally carries weight.
  Off keeps everything and offers nothing; uninstalled is refused while there is anything to
  take away with the code.

  **And an archive cannot be quietly incomplete.** A plugin that owns a person's data says how
  to put it in their export; one that owns data and cannot say gets its models *named* in the
  archive under `not_carried`, because a silent gap is discovered on restore and a stated one
  while the original still exists. A plugin that raises while exporting is named the same way
  rather than taking somebody's whole archive down with it. `FORMAT_VERSION` is 8.

  The refusal lives on `remove()` and not only on the page, so a management command or a shell
  meets the same rule. `CONTRIBUTING.md` has the rule for plugin authors, including why
  `export_for` is not optional in spirit and why owning a table means shipping migrations. (#128)

- **An instance can offer signing in with a code sent to the primary address.** allauth has
  carried the feature all along and it was switched off; what was missing was the decisions.
  *Server settings → Sign-in* now has the switch, off until an administrator says otherwise.

  **A code and not a link, which is where this parts company with the request.** A link is a
  bearer credential that works from anywhere, and corporate mail scanners *follow links* —
  Defender, Proofpoint and their kind fetch every URL in a message, which spends a single-use
  link before the recipient has read the mail. Postulo's people correspond with recruiters, so
  some of them have exactly that mail. A code typed back into the browser that asked for it
  cannot be burned by a scanner, cannot be usefully forwarded, and cannot be used from a device
  that is not the one signing in.

  **It is never a second factor, and there is no setting to make it one.** Somebody with an
  authenticator is still asked for it — asserted in `tests/security/` rather than assumed,
  with its companion proving the flow works at all so the assertion is about the factor and
  not about a broken path. This is a deliberate departure from `sso_is_second_factor`: that
  setting exists because an identity provider may itself have checked identity carefully and
  Postulo cannot see how, while a code out of an inbox has no provider behind it to trust. So
  there is nothing for an operator to decide and no switch to leave in the wrong position.

  **Offered only where mail is actually delivering**, not merely configured — which is what
  the mail-health work was for. A sign-in page promising something it cannot do, to somebody
  who may have no other way in, is the worst place to be optimistic. The switch and the page
  both read the same answer, and both close again if mail starts failing.

  Three minutes, three guesses, three resends, and the browser is never remembered — a one-off
  code must not become a standing credential on a machine that may not be theirs next week.
  The code goes to the primary address, so *Settings → Account* now says that changing which
  address is primary moves it. And the whole thing makes the mailbox more load-bearing, which
  the settings page says in as many words rather than leaving to be discovered. (#153)

- **A confirmed telephone number can be the way back into an account**, joining email and a
  passkey — and on this release no number is one anywhere, because nothing can confirm a
  number until an operator installs a text gateway. The route exists and refuses to pretend,
  which is the same shape the two pieces underneath it took.

  **It is deliberately not the primary number.** The primary is what a CV and a letter print
  and what a recruiter dials; making the same row the way back in would mean somebody who
  changes the number on their CV silently changes how they prove they are themselves. A
  separate choice removes the coupling instead of warning about it, and one per account is
  enforced by a partial unique index rather than by whichever form saved last.

  **Only a confirmed number of your own.** A recruiter's switchboard is a number an account
  recorded, never one it is, and a number nobody answered on is a claim rather than a channel.
  Both refusals sit on the model rather than only on the form, because the API, a management
  command and a shell all reach it. Editing the digits takes the nomination away with the
  confirmation it rested on, in the same breath.

  **Switching the plugin off cannot remove somebody's way back in.** That is a boundary worth
  naming: recovery is instance policy and a feature plugin is a per-person preference, so an
  administrator switching *several telephone numbers* off for one account must not quietly
  decide whether that account is recoverable — which is the failure the transport interlock
  exists to prevent, arriving through a different door. The plugin governs what is shown and
  used, exactly as it always has, and recovery reads past it.

  The count of accounts with nothing but email grows a second clause rather than an
  assumption, and both halves are required: an account has a route when it has nominated a
  confirmed number **and** the instance has something that can reach one. An archive cannot
  carry a nomination in either — the importer drops it with the confirmation it depends on,
  or a file could nominate a way into an account on an instance that never checked the number.
  `FORMAT_VERSION` is 7. (#144)

- **An administrator can issue a way back into an account, so mail is no longer the only
  one.** Email was the single route, which made the interlock refusing to switch the mail
  transport off while it is the last way in correct *and* permanent: on an instance with no
  second route it could never open. Now it can.

  The route needs no third party at all, and that is why it is first of the three candidates
  rather than SMS. An administrator makes a single-use link and hands it over by whatever
  means they already trust — in person, on the telephone. No gateway, no vendor, no cost, and
  it is the answer for the family or small-team instance where the administrator is in the
  same room.

  A link like this is a whole account in a URL, so four properties carry the weight.
  **Short-lived** — an hour, because a URL that still works next week has been sitting in a
  chat log for a week. **Single-use**, and *opening* it does not spend it: a link-preview bot
  in the chat an administrator sent it through would otherwise burn somebody's only way back
  in by fetching it. **Shown once**, to the administrator who asked, and never mailed, logged
  or displayed again — handing it over is their job, and doing it over the channel this
  replaces would be absurd. **Recorded**, and the record outlives the link: who issued it, for
  whom, when, and what became of it, because taking somebody's account back should not be
  possible without a trace.

  **It sets a password; it does not sign anybody in.** The smallest blast radius available.
  A person who uses one still signs in afterwards and still meets their second factor, so a
  link that goes astray is a password change on an account whose other factors are untouched.
  The token never appears in the address of the form it leads to either, so it does not travel
  in a `Referer` header or a synced browser history.

  **An administrator cannot usefully issue one for themselves**, because making one needs
  signing in and the person who forgot their password cannot. So the route reaches everybody
  *but* the issuer: two administrators cover each other, one administrator covers everybody
  else, and a lone administrator without a passkey is the single account it does not reach.
  Counted rather than assumed, so the answer changes the moment somebody is made an
  administrator.

  **And it covers getting existing people back in, not admitting new ones.** A new account
  still has to verify an address before it exists, so an instance with mail off can recover
  its people and cannot take on more. The Email page says that where somebody is about to act
  on the lock being open, rather than leaving them to discover it when the first sign-up
  fails. (#103)

- **Postulo can carry a text message, and ships nothing that sends one.** Both halves are
  the feature. There was no SMS anywhere — one comment naming it as a route that had not
  landed — and a telephone number cannot be confirmed until something can reach one.

  **It is a transport, not a notifier, and that was most of the decision.** A notifier's
  credentials belong to the person: their Twilio account, their Apprise endpoint. Somebody
  locked out of their account is exactly the one whose own gateway may be unreachable, and
  "the account holder configured the channel that proves they are the account holder" is
  circular. A channel carrying a way back in has to be operated by the instance — which is
  what a transport already is.

  **One kind with two mediums, not a second kind.** Selection, configuration, the interlock
  that stops the last way in being switched off, the exemption from per-person policy and the
  page that lists them are identical for both; what differs is the payload. That is what a
  field describes and what a kind would have duplicated. A transport that does not say what it
  carries carries mail, so nothing written before this has to be edited.

  **No gateway ships, and that is the answer rather than a gap.** Every one of them is
  somebody else's jurisdiction: a number, a message and a timestamp reaching Twilio, Vonage or
  a national aggregator on every send, from an application whose argument is that a
  self-hoster's data answers to them. Some operators will refuse that and they are right to.
  The kind exists; the package is somebody else's, exactly as it is for mail over an HTTP API.

  **And SMS is deliberately not the first way back into an account.** It needs a third party,
  costs money per message, and is defeated by a SIM swap. An administrator issuing a recovery
  link needs nobody else and answers the same question for the self-hosted instance with one
  administrator. So a text message becomes a recovery route only when there is a gateway *and*
  every active account has a confirmed number — the same bar the passkey route is held to, and
  for the same reason: this list decides whether mail may be switched off, so a route covering
  nine accounts in ten would strand the tenth.

  Two rate limits rather than one, because two different mistakes want bounding: per account
  is somebody driving a resend button, per number is a stranger whose number was mistyped into
  a form and who has no way to make it stop. Mail that fails costs nothing; this costs money
  and annoys somebody who never asked to be involved. A gateway failure is logged **without
  the number or the message body**, because that line ends up in an operator's log and the
  body is a code that gets somebody into an account. (#143)

- **One contract for every kind of contact detail, so they stop each having their own.**
  Postulo holds three and they were at three different stages: an address that allauth
  validates and confirms over SMTP, a telephone number checked less than syntactically on
  purpose and confirmed by nothing, and a postal address that is neither. Nothing could ask
  them the same question — and the question is about to matter, because a number that becomes
  somebody's way back into their account has to be one they proved they hold.

  **Checking now has named depths, and the middle one is refused on purpose.** *Shape* —
  does this parse. *Plausible* — is this dialling range assigned, does this domain publish an
  MX record. *Real* — did somebody answer. `phones.py` refused the middle depth because it
  needs the numbering plan of every country and Postulo was never going to dial anything.
  Half that argument expires the moment a number is a channel Postulo sends a code to; the
  other half does not. The cost is unchanged, and the thing that settles the question is
  confirmation, which is free and conclusive. So the refusal is made again, deliberately,
  rather than inherited.

  What did change is that telephone checking is now *syntactic* rather than less: a number
  carries a country and between four and fifteen digits, which is what E.164 allows. **A
  number that fails is still saved** — the number a recruiter dictated over a bad line is
  still the only one anybody has, and refusing to record it would be the worst outcome
  available. The check reports; the one caller that will refuse is the one asking whether a
  number may be a way back in.

  **Validated-but-never-provable is a legitimate answer.** A postal address can only be
  proved by posting something to it, which this project is not going to build, and a contract
  treating that as a gap would keep the postal channel looking permanently unfinished. It is
  kept apart from a second kind of empty answer — a channel that *is* provable and has nothing
  to prove it with yet, which is where telephone numbers sit until something can reach one.
  Two different facts, and the code that has to explain itself to somebody locked out of their
  account needs to tell them apart.

  **allauth is described, not rewritten.** The email channel satisfies the contract by
  delegation: allauth keeps the token, the expiry, the resend, the link and the verified flag,
  and this says so. A contract an existing working implementation cannot be described by is a
  contract nobody adopts.

  A channel names *what would carry* its confirmation rather than reaching for something, so
  the interlock protecting the mail transport has the same answer to read. `POSTULO_CONFIRMATION_RATE`
  bounds resends across every kind at once, because the kind that costs money per send is not
  the one whose own setting anybody would remember to configure. And the one line a code
  arrives on names the instance and says nobody will ask for it — a stranger's message
  containing six digits is the shape of every scam there is. (#146)

- **An instance can offer fewer languages than Postulo speaks.** Thirty-nine of them in one
  picker is a long list for an instance whose people all read two, and until now there was no
  way to say so short of deleting catalogues from the image. *Server settings → Defaults*
  now carries the list; untick a language and it leaves everybody's picker.

  **Empty means all of them, and that is the design rather than a shortcut.** Storing the list
  of every language ticked today would have frozen the set on the day somebody first opened
  the form: the instance would go on offering exactly those thirty-nine while a later release
  added a fortieth that nobody would ever see. Ticking them all stores nothing, so an instance
  nobody has narrowed keeps gaining languages as Postulo does.

  **Narrowing never rewrites what somebody chose.** A person whose profile says French reads
  the instance default from the next page onwards once French stops being offered — and their
  setting stays exactly where it is, so offering French again brings them back without their
  noticing either event. Blanking the profile instead would have been one line shorter and
  irreversible: an operator narrowing thirty-nine to four would silently overwrite every
  account that had chosen one of the other thirty-five, and there is nothing to put back.

  Two saves are refused rather than accepted quietly — offering nothing, which leaves nobody
  able to read anything, and withdrawing the language new accounts start in, where the message
  names that language and says to change the default first. An administrator is not exempt
  from the narrowing either: what the instance offers is what the instance offers, and two
  rules where one will do is how the two drift apart. (#120)

- **A contact can be in a department.** Which team somebody is in was going into `role`
  — "Engineering — hiring manager" — or into the notes, or nowhere, because there was
  nowhere else. Now there is: type the name on the contact form and it joins that company's
  list, type one already there and it is reused, clear the box and the person leaves the
  team while the team stays.

  **A department is deliberately not a company with a parent**, though it would have fitted
  the relation added beside it. It has no website, no logo, no identifiers, no industries and
  no postings of its own that are not the company's; modelling it as a company would fill the
  companies list with things nobody applied to and teach every count to exclude them. More
  code, fewer lies.

  Both ends are optional, which is the normal case — most contacts have no department, and a
  department with nobody in it is a team somebody applied to before knowing anybody there.
  Going away takes the right things in each direction: a department deleted leaves its people
  where they work, because they are still at the company, and a company deleted takes its
  departments with it. A contact cannot borrow another company's department, and saying so is
  a sentence rather than a silent correction.

  Typed rather than chosen, because making somebody create a team before they can name one is
  a form standing in front of a form — the same reasoning `Industry.named` already follows.
  (#137)

- **A company can be part of another one.** Applying to Google, to DeepMind and to Waymo is
  applying to three companies the person already knows are one group; Postulo counted three
  unrelated employers, and searching for the group's name found none of them. One nullable
  `parent` on `Company` fixes that: a tree rather than a graph, because at most one owner is
  what an ownership structure *is* and it keeps every question answerable by walking rather
  than searching.

  **Nothing is inherited**, which was the open question in the issue and is answered *no*. A
  subsidiary keeps its own industries, logo and notes. Inheritance is a rule people then have
  to hold in their heads; naming the parent on the page is what the reader wanted.

  **Three refusals, each with the reason.** A company cannot be part of itself. A chain
  cannot close into a loop — and the message names the company whose link would close it,
  because "not allowed" leaves somebody looking down a list of subsidiaries guessing which
  one. And a chain cannot exceed ten, which is far past any real group and stops a mistake
  becoming a page that walks for ever. The form goes further and does not *offer* what would
  be refused: a company's own subtree is not in its parent list, since refusing something the
  form suggested is worse than never suggesting it.

  The walk protects itself as well as the form does. `clean()` cannot run on a
  `QuerySet.update`, so a loop written straight into the database — by an import, by a
  migration, by hand — would otherwise hang every page that asked which group a company
  belongs to. `group` and `descendants()` both stop rather than spin, and a test writes that
  loop deliberately to prove it.

  The export is format 5: a company names its parent **by name**, because identifiers in an
  archive are local to the file, and the importer resolves them in a second pass once every
  company in it exists — a child may be read long before its parent is. An archive written
  before this names no parent and still imports. (#55)

- **Several telephone numbers per person and per contact, behind a plugin that can be
  switched off.** `Profile.phone` and `Contact.phone` were one `CharField` each: one number,
  no primary, no uniqueness. Email addresses have worked the way this asks since allauth
  arrived — several per account, exactly one primary, unique across the instance — so there
  was a shape to copy and its edges were already known.

  **It is a plugin, and that is a new kind.** Every plugin kind until now talks to something
  outside: a source reads a page, a notifier sends, a store keeps files, a sync exchanges, a
  transport mails, an importer reads a file. A **feature** talks to nothing. It answers one
  question — *is this on for this person* — and the application asks before offering the part
  of itself the feature covers. It exists as a plugin rather than as a settings checkbox
  because Postulo already has one place where a capability is switched on for a person,
  overridden by an administrator, and explained on their settings page with a record of who
  decided; a second mechanism for the same question would be a second set of edge cases and a
  second thing to remember at every call site.

  **Switching it off deletes nothing, and that was the point worth arguing about.** The
  interface promises exactly that, on two pages, in every language Postulo speaks, and the
  first kind whose subject is Postulo's own tables is not where that promise gets an
  exception. Off shows and uses the primary number only — precisely what the single field did
  — the page says how many others are being kept back so nobody concludes a checkbox ate
  them, an export carries all of them either way, and switching it on again finds them in the
  order they were left. Saving the one box while it is off touches the primary row and
  nothing else: rows a person cannot see are not theirs to lose by saving a form that never
  showed them.

  **A number is kept once across the whole instance**, which is the maintainer's decision and
  a disclosure worth naming rather than dressing up. Refusing a number because somebody
  already holds it tells whoever typed it that *an account on this server has it*. There is
  no way to enforce the rule without saying so, and a vaguer message would disclose exactly as
  much while leaving the person guessing, so the message says it in those words. Nothing else
  is disclosed — not whose, not where, not when. Two things sit outside the rule by design: a
  number that never reached international form has nothing comparable and collides with
  nothing, and pairs that were already recorded before the rule arrived are grandfathered, the
  migration keeping both and the form telling you the first time one is edited. Unique but
  **unverified**, because Postulo cannot send an SMS and should not start; the first account
  to type a number holds it.

  The rows are a generic relation for the reason `CVItem` gives — the holder is heterogeneous
  and two nullable foreign keys would need a migration every time a third kind of holder
  appears — with a `GenericRelation` on each holder so deleting one takes its numbers with it.
  The primary is a partial unique index rather than a rule a form remembers, and the radio
  that chooses it names a form prefix rather than a key, because somebody adding their first
  two numbers is choosing between rows that have no keys yet. The export format is 4: a
  `phone_numbers` list where a `phone` string used to be, and the importer still reads an
  archive made yesterday. (#90)

- **Postulo now speaks Ukrainian and Turkish.** The catalogue set stopped at the borders of
  the European Union, which is a political boundary rather than a linguistic one: it left out
  the language with the most speakers of any in Europe that the Union does not administer, and
  it left out Turkish, which sits inside several member states without being official in any
  of them. Neither omission was ever a decision — Europe was simply taken to mean the Union
  because that was the list that came to hand.

  **Why the continent comes first is a decision, and this is it.** The maintainer chose to
  give the languages of continental Europe precedence over the rest of the world, in the
  service of European digital sovereignty: Postulo exists as an alternative to hosted
  services that answer to somebody else's jurisdiction, and an alternative that only speaks
  the languages those services already speak has not moved anybody very far. Africa (#70)
  and the rest of the world follow in later releases; the order is deliberate, not
  alphabetical.

  **Ukrainian brought back the plural rule that has to be got right.** Three forms, chosen by
  the last digit rather than the value: 1, 21 and 101 take the first, 2–4 the second, and
  11–14 the third *despite ending in 1 to 4*. Copy the two-form rule and every count on
  every page is wrong for four numbers in ten. Turkish went the other way and is worth saying
  out loud, because the tidy answer is also the wrong one: the noun after a numeral does not
  inflect at all — *bir başvuru*, *iki başvuru* — so both forms carry the same word, and the
  split earns its keep only in the rest of the sentence around it.

  **What comes next is what stops being tidy**, so the ground was laid here rather than at the
  language that first needs it. `FLAG_COUNTRIES` used to be able to claim that every language
  had one uncontested home, which was true of the Union and is not true of the continent:
  Basque, Catalan, Galician and Welsh are coming, and each sits in a state whose flag already
  stands for a different language on the same list. The rule written down at this point was
  that such a language carries none: `flag_country()` answers with nothing and the picker
  closes the row up — behaviour already there, written for a phase beyond Europe that turned
  out to begin inside it. It did not survive contact with Catalan; four paragraphs down is
  what replaced it.

  **Norwegian Bokmål and Icelandic followed**, and the second is the one that repays reading
  the rule rather than the form count. Icelandic has two plural forms, so `_TWO` looks right
  and is wrong: the *last digit* decides, not the value. 21 and 31 take the singular like 1 —
  *tuttugu og ein umsókn* — while 11 takes the plural like 12. A two-form language whose rule
  is not `n != 1` is exactly the case a form count cannot catch, which is why the rule is
  written out per language instead of inferred.

  **Bosnian and Serbian brought the second script**, and a temptation worth naming. Both
  take the same three-form rule Croatian already carries, and the tidy move is one shared
  constant for all three — which is exactly how a language ends up inheriting a rule nobody
  checked for it. They are written out separately. Serbian is offered in Cyrillic, its own
  name in the picker reading *српски*, so the list now carries three scripts and every one
  of them still says which language it is in.

  **Albanian and Macedonian** finish the Balkans. Macedonian is the third language here whose
  rule is two forms decided by the last digit rather than the value — Icelandic's shape, in a
  different family — which is now enough of a pattern to say out loud: `nplurals=2` says
  nothing about *which* two.

  **Georgian and Armenian each bring an alphabet of their own**, used by no other language on
  the list and by almost none anywhere else. That costs nothing at render time — the interface
  has carried `lang` on every language option since #43's first pass, for WCAG 3.1.2 — and it
  is worth naming because the list is now a place where somebody who cannot read the current
  language has to find their own, and *ქართული* and *հայերեն* are unmistakable to the person
  looking for them in a way an English label never is. Armenian also does not take `_TWO`:
  it counts zero with the singular, the way French does.

  **Catalan and Basque made the flags admit that a language can be at home somewhere that is
  not a state.** The table beside the picker had always mapped a language to an ISO 3166-1
  country, and for the Union that worked because every language there had one. Catalan's
  honest answer is not Spain — that flag already stands for Spanish, two rows up — and the
  rule written down for this case said the language would simply get no flag. That was the
  wrong call, and the maintainer said so: it is `ES-CT`, Catalonia's own, and Basque's is
  `ES-PV`, the ikurriña. So the flag table, `assets/flags.txt`, the tag's validation and the
  sync script all take ISO 3166-2 subdivisions now, Galician and Welsh will use theirs when
  they arrive, and the telephone field is untouched — a dialling code is a state's, and
  Catalan's is still Spain's.

  **Galician and Welsh arrived to find their flags waiting**, `ES-GA` and `GB-WLS`, which is
  why the table was widened a commit early rather than at the language that finally forced
  it. Welsh brought the plural rule nothing about a form count predicts: four forms, and the
  only rule on this list that singles out particular numbers. 1 and 2 take their own, 8 and
  11 share the fourth, and every other number falls to the third. There is no reasoning from
  `nplurals=4` to that shape — it is copied from the language or it is wrong.

  **Luxembourgish finishes the fifteen**, and it makes the point the whole issue rests on
  better than any of the others: it is a national language of a member state that is not a
  language of the Union, because Luxembourg files its Union business in French and German.
  The Union's list and Europe's list were never the same list. Thirty-nine catalogues now,
  every one of them complete, across three alphabets, with four subdivision flags beside the
  thirty-five countries.

  The test that pinned how many languages there are was **deleted rather than edited fifteen
  times**. A number in a test is a fact restated in a second place, and every language in
  this issue would have had to visit it; what has to be true is that `settings.LANGUAGES`
  offers exactly what the table holds, and that is what it now asserts. (#118)

- **The languages of Africa, and everything a language needs before its words arrive.**
  "Every language of Africa" is some two thousand of them, so the rule drawn is *official
  or national status in at least one African state, plus the cross-border lingua francas
  that outrank most of those in speakers*: twenty-nine languages, and a documented rule
  rather than a list assembled by feel. Each arrives with the things that have to be right
  **before** anybody translates against them and are expensive to correct afterwards — its
  own name for itself, its gettext plural rule (Arabic has six forms; Wolof, Igbo, Shona
  and Bamanankan have one), and its script. Arabic is the first right-to-left language
  Postulo carries, and the layout for it landed first (#67). **Thirteen carry no flag, and
  that is the answer rather than a gap**: Europe's stateless languages got the flag of the
  place they are actually at home in — Catalonia's, the ikurriña — and these are the ones
  where not even that would be honest. Arabic is twenty-two countries, Swahili four, Hausa
  two, Sesotho is Lesotho's as much as South Africa's; a wrong flag against somebody's
  language is not a small wrong. **The image can now draw them**: it installed
  `fonts-dejavu-core`, which covers Latin, Greek and Cyrillic and stops there, so an
  Amharic or Tigrinya CV would have rendered as a page of empty boxes; `fonts-noto-core`
  is added and a test holds the image's fonts to the languages actually offered. **A
  language is offered once somebody has begun its catalogue, and not before** — all
  twenty-nine exist and are empty, and listing them would be offering somebody their own
  language and handing them an English interface. Translate one string and it appears,
  with its completion beside its name. The suite's rule changed to match: the European
  Union set must stay complete, every language must have a correct plural rule and a page
  that renders, and completeness is a promise about a finished phase rather than about a
  language added yesterday. The 45,037 translations themselves are the rest of #70. (#70)

- **The interface is laid out right to left when the language is.** The `dir` attribute
  has been emitted since the first release and has been `ltr` on every page ever rendered,
  because every language Postulo speaks is read left to right — so what happened under
  `rtl` was unknown rather than known-good. It is known now, and the layout work is done
  before the first such language arrives (#70) rather than after somebody reports it.
  Sixty-five classes across forty-two templates and the stylesheet stopped naming a side
  and started naming a **reading edge** — `ms`/`me`, `ps`/`pe`, `start`/`end`,
  `text-start`/`text-end`, `border-s` — which mean exactly what they meant under `ltr`. The
  action buttons at the end of a heading row, the timeline rule beside the event log, the
  menus that hang from a corner, the skip link and the board's columns all move to the
  other edge; icons that point sideways are mirrored by name, and vertical ones are left
  alone because they mean the same either way. **Text that is not in the reader's script
  is isolated**: company names, job titles, addresses and letter subjects in `<bdi>`, and
  every `<code>` span by one stylesheet rule, so a Latin name inside an Arabic line does
  not throw the separator to the wrong end. **A rendered document has its own direction**,
  from the language it is written in and not from the language of whoever made it — an
  English CV written by somebody using Postulo in Arabic is an English, left-to-right PDF.
  One list of right-to-left subtags serves the interface and the documents alike, and it is
  Postulo's own rather than Django's, because #43 goes well past the languages Django ships
  with. Held by two things that will still be there next year: a **lint** that fails on any
  template or stylesheet class naming a left or a right, and a **browser suite** that reads
  the application in a right-to-left language — in both themes, through axe — and measures
  that the buttons, the board and the skip link actually moved. (#67)

- **Every plugin carries a manifest, and the six Postulo ships fill it in.** #97 settled
  what a plugin declares — a short name, a full name, an author, a version, a description, a
  source link — and then Postulo's own declared almost none of it. `SchemaOrgSource` was two
  lines, `name` and `version = "1.0"`, under a module that says a source's version exists *so
  a capture can be traced to the code that made it*. It could not be traced to anything:
  `"1.0"` meant 1.0 the day it was written and would have gone on meaning it through every
  change to the parser underneath. A rule this project asks of other people and not of itself
  is not a rule.

  **The shape changed with it.** The facts now live in one `Manifest` rather than one
  optional attribute each, with `@declares` attaching it and setting the protocol members
  *from* it, so the identifier the registry keys on and the one in the manifest cannot
  disagree. That is not tidiness. A loose attribute per fact can never be required —
  `runtime_checkable` protocols check data members, and the registry drops anything failing
  `isinstance`, so adding `label` to `SourcePlugin` would have silently unloaded every source
  anybody had already written. One optional attribute carrying any number of facts has
  neither problem, and a field added later costs a plugin that has not heard of it nothing.

  `manifest_of()` is now the single place anything asks who a plugin is, and it looks in
  three: the manifest, the loose attributes plugins used before there was one, and **the
  wheel the plugin was installed from**. That last one is what makes "one place to look" true
  rather than aspirational — a third-party plugin that never heard of manifests still has an
  author and a licence in its packaging, and Postulo already reads them.

  So *Server settings → Plugins* now shows the version, the author, the licence and a link to
  the source **for every plugin**, built-in or installed, on the same line in the same place.
  #97's complaint was that an administrator could see who wrote a plugin on the day they
  installed it and never again; the built-ins could not be seen even then. The mail transport
  is the one plugin that page does not list — it is not governed per person — so the same
  line appears on *Email*, beside the transport carrying the mail.

  Their version is Postulo's own, which is the truth: these ship with the application and
  change when it does. **Their identifiers did not move.** `schema.org` and `page-metadata`
  are written into the `source` field of every capture anybody has made, and renaming one
  orphans that history; there is a test pinning them. There is also a test that walks the
  built-ins rather than naming them and fails on a missing field — which is the part that
  matters, because the issue asking for this counted four and there are six. (#98)

- **Delivering mail is a plugin now, and SMTP is the one that ships.** A new kind,
  `transport`, under `postulo.transports`. It exists for a specific person: the self-hoster
  whose provider blocks outbound 25, 465 and 587 — most residential connections and several
  hosts — who today has no route except finding a relay that speaks SMTP. They can install
  one that speaks an HTTP API instead. Postulo ships exactly one transport and names no
  vendor, which is the plugin system doing what it says it is for.

  **A plugin cannot supply `MAILERS`**, and that shapes the whole thing. Django reads that
  setting when the settings module is imported; entry points are not loaded until the app
  registry is ready, which is later. So "SMTP is a plugin" cannot mean "the plugin defines
  the mail settings". What it does mean: core names one backend, and that backend asks which
  transport is selected and how it is configured **at send time** — the same mechanism the
  Email page needed anyway, so the two are one piece of work rather than two.

  **Installing a transport does not silently redirect the mail.** The registry prefers
  third-party plugins for sources, because a plugin written for one job board knows more
  about it than a general parser does; that argument does not transfer to where an
  instance's mail goes. An administrator chooses, and until they do the built-in one carries
  it. A choice naming something no longer installed falls back rather than failing.

  **The lock.** A transport may not be switched off — nor its package removed — while it is
  the last way anybody could get back into their account. That is written as a rule that is
  *evaluated*, never as `if plugin == "smtp": refuse`. A hardcoded exception is one nobody
  deletes, so the day another recovery route lands the lock would stay shut out of inertia,
  and an instance running a second transport should be able to switch this one off.
  `recovery_routes()` lists the ways in that exist: email, and a passkey, which signs
  somebody in without the password they have forgotten. A two-factor recovery code is
  deliberately not on that list — it is a *second* factor, so it helps somebody who still
  knows their password and does nothing for somebody who does not. The refusal counts the
  accounts it is protecting and names them, the way *People* refuses to remove the last
  administrator, and it opens by itself the moment the list has something else in it.

  **A transport is nobody's to decide.** The per-person policy has four states and none of
  them means anything about mail delivery; *forced off* would be an account nobody can
  recover. All four are refused for a transport rather than merely left off the page,
  because the page is not the boundary.

  **Three things this did not disturb.** The environment is still a complete route, so a
  fresh instance with an empty database can send the verification email that has to come
  before the first account exists. SMTP keeps its own named columns rather than the generic
  configuration blob, because each is overridden individually by its own variable and
  because of that first boot. And the email *notifier* is still a different plugin: it
  decides something is worth telling somebody and writes the words, the transport gets those
  words off the machine, and merging them would make notification settings and delivery
  settings the same form. (#104)

- **Email can be configured from the interface, and the environment still wins.** *Server
  settings → Email* was a read-only summary and a *Send a test message* button, so changing
  where an instance sends mail meant editing a file and restarting a container — on an
  application whose whole premise is that you run it yourself, often on a machine you reach
  through a browser and nothing else. The server, port, username, password, STARTTLS,
  timeout and from-address are now all on that page, and a `.env` written for 0.1.0 goes on
  meaning exactly what it meant: where a variable is set it wins, and the field shows the
  value **read-only, with the variable that pins it named beside it**, rather than an empty
  box and a shrug. Readonly rather than disabled, deliberately: a disabled input leaves the
  tab order and is announced inconsistently, and a value an administrator wants to copy
  across to their relay's own configuration must not be one some of them cannot reach.
  Pinned fields are also **dropped on the server**, whatever a request contains, because a
  readonly attribute is presentation and a form can be posted without a browser.

  The engineering is not the form. **`MAILERS` is built once, when settings are imported**,
  so a page that wrote to it would have saved, said so, and changed nothing until a restart
  — worse than no page. Django builds a fresh backend for every message and caches none, so
  Postulo's own backend resolves the settings then; the from-address is stamped there too,
  because `DEFAULT_FROM_EMAIL` is read at send time by Django's code and allauth's and
  neither offers a hook. **The password is encrypted at rest** under the same key as a
  plugin connection's secrets, and is never rendered again — not the value, not its length;
  the page says only whether one is set, and forgetting one is a separate, deliberate
  checkbox rather than an empty field.

  Beside *Send a test message* there is now ***Test the connection***: it opens the socket,
  negotiates STARTTLS, signs in, asks the server to do nothing and hangs up, proving the
  credentials without sending anybody an email they then have to ignore. It takes **what is
  on the screen**, not what is stored, so a new relay can be tried without first overwriting
  the one that works; and a failure does not block saving, because an administrator may be
  configuring a relay that is not up yet. The SMTP host is deliberately **not** subject to
  the private-address rule that governs capture — that rule exists because a capture URL
  comes off a stranger's page, and a relay on `10.0.0.0/8` is the ordinary case here.

  One state gets said out loud rather than left to be discovered: when a setting is **both**
  stored here and pinned by a variable, the page says so, because removing that variable
  hands over to the stored value and mail starts going somewhere else without anybody having
  edited anything. Only STARTTLS is supported; implicit TLS on port 465 is not offered yet,
  here or in the environment, and the field says so. (#84)

- **Postulo speaks Brazilian Portuguese.** Not a copy of the European catalogue under
  another code, and not a second translation from English either: **seeded from `pt-pt` and
  adapted**, because every string had already been translated once by somebody thinking about
  this application and what a variant wants is that work carried across. Each of the 1,615
  entries carries the `draft` flag, where it means something precise — *this came from the
  other catalogue and nobody who speaks this one has read it*. A mechanical pass then changed
  the terms that are simply different words — *ficheiro* to *arquivo*, *palavra-passe* to
  *senha*, *definições* to *configurações* — word-boundary anchored, because `rato` is
  Brazilian *mouse* and also the tail of *contrato*, and an unanchored substitution produced
  "Cont**mouse** a termo" before the boundary went in. Where the Portuguese was ambiguous the
  English settled it: *Guardar* is sometimes "keeps" and sometimes "Save", and only the msgid
  knows which. `ligação` — which means both a *Connection* and a *link* here — and every
  question of gerund, clitic and register were **left alone on purpose**, because they are a
  speaker's to answer. The plural rule is `n > 1` and not the European `n != 1`: Brazilian
  treats zero as plural, and copying that one line without looking would have made every
  count on every page ungrammatical for the language's largest population. The picker's
  heading changed with it, from *Machine translation, awaiting review* to **Awaiting review by
  a speaker** — a catalogue seeded from its sibling is not machine-made, and what every
  language in that group actually has in common is that nobody has read it yet. (#110)

- **A person can see which plugins are running for their account, and who decided.**
  *Settings → Plugins*, a new section. *Connections* answers "what have I set up"; it says
  nothing about the parsers that read a posting off a page — they need no connection, so they
  appeared nowhere at all — and nothing about **why** a plugin is available. This page exists
  mainly for one of its rows: the one an administrator decided. A control that is not yours to
  change is shown **disabled with the reason beside it and their name**, rather than hidden,
  because hiding it would be the quiet version of exactly what the page is against. A plugin
  made *unavailable* does not appear, which is what unavailable means; one forced *off* does,
  because being told it was switched off for you is the whole difference between the two. A
  form and a Save button, so it works with no JavaScript at all. The page says **per account,
  not per browser** — the request had asked for "in their session", and plugin state survives
  signing out and does not change when you sign in elsewhere; a page saying *session* while
  meaning *account* would teach people something untrue about where their settings live.
  (#96)

- **An administrator can decide a plugin for a person, and cannot decide it quietly.** Four
  states — available, unavailable, always on, always off — set for the whole instance under
  *Server settings → Plugins*, or for one account from its row under *People*. Three of the
  plugin kinds exist to move somebody's data elsewhere, so this is a real power over another
  person's account, and what makes it acceptable is that it is visible: whatever is decided is
  shown to that person with the name of who decided it. **Unavailable and always-off are
  deliberately different** — the first means *not part of your Postulo*, the second means *you
  can see this exists and somebody switched it off for you*, and the second is the more honest
  of the two. **Nothing is ever deleted.** Switching a plugin off stops it being used;
  connections and their configuration stay exactly where they were, and reversing the decision
  brings them back unchanged — a policy that destroyed data on the way would be a delete button
  with a confusing name. A person's own choice survives being overruled and returns when the
  overrule is lifted, and there is a test for that. **Forcing a plugin on does not make it
  run**: a notifier, store or sync needs credentials only its owner can supply, so *on* means
  "available to you and you may not switch it off" — the page says so rather than leaving it to
  be discovered. Mail transports are exempt from all of it, because delivery is instance
  infrastructure and *always off* for one would be an account nobody could recover. Who decided
  and when live on the row, and every change is logged: *Server settings* has no audit trail of
  any kind, and a decision about what somebody's account may run was the wrong place to wait
  for one. (#95)

- **Plugin repositories are rows an administrator manages, not one environment variable.**
  Catalogues already worked — a signed index, an Ed25519 key, a checksum for every wheel, and
  several of them supported at once. What did not exist was any way to manage one: they came
  from `POSTULO_PLUGIN_CATALOGUES` as `name|url|key`, so adding a catalogue meant editing a
  file and restarting the container. *Server settings → Plugins* now lists them in three
  tiers. **Internal** is the plugins that ship inside Postulo, shown as a repository so the
  list reads as one thing and synthesised rather than stored — a row would be a fact about a
  place, and there is no place. **Official** is one row, and it ships **switched off and
  pointing nowhere**, because Postulo publishes no catalogue: doing so means a signing key
  kept safe for the life of the project and an answer for rotating it if it leaks, which is
  taken deliberately or not at all. **Custom** is however many an operator wants. The
  environment still wins and is shown doing so, the way four settings already work. A key
  is checked when it is typed rather than only when a fetch fails weeks later, and replacing
  one says so plainly and is written to the log — it is not an edit like changing a label, it
  replaces the only thing standing between an index and code running here. Switching a
  repository off stops installing and updating from it and **does not touch what it already
  installed**; that code is on the volume and the registry never consults a catalogue. There
  is a test for each of those, and a migration that turns whatever an instance already had in
  its environment into rows, so nobody loses a catalogue. (#93)

### 🐛 Fixed

- **The report page was missing two of its own classes, and the stylesheet was being fed by
  prose.** The report arrived without a stylesheet rebuild, so its *Show this period* button
  sat out of line and its two tallies stacked on a wide screen, because `mt-5` and
  `lg:grid-cols-2` compiled to nothing. Only CI noticed, after the push; the ordinary suite
  now rebuilds the stylesheet and compares wherever the Tailwind CLI is installed, and the
  build is on the checklist.

  Chasing a third, phantom class turned up the real problem: **what compiled depended on
  more than the interface.** Tailwind's automatic detection scanned the whole repository, so a
  word in a test's docstring, the wiki or the changelog could put a class in the stylesheet
  every page loads — the template lint's own list of forbidden physical utilities was
  compiling them. The compiled file sat inside the scanned tree, so a build written anywhere
  but over it read the previous build as a source. And the templates that become PDFs, which
  never load this stylesheet, had the words in their inline CSS read as classes. The scan is
  now the interface and nothing else, and twenty-four utilities nothing used — among them
  every physical one — have gone. (#164)

- **The image builds again.** Giving the Python build a stage of its own (#157) put the
  dependencies first, so that a change to the application would not re-resolve them, and
  left the one `uv sync` above the line that copies the source — and `uv sync` installs the
  project as well. Every image build since failed with *Expected a Python module at*
  `src/postulo/__init__.py`, and nobody knew, because nothing in CI builds an image (#81):
  the test instance's deploy was the first build since. The dependencies are synced before
  the source with `--no-install-project`, Postulo after it, and a test reads the Dockerfile's
  stages to hold that order. (#166)

- **Pages that ran off a phone's screen in a longer language, and words squeezed out of their
  own space.** The arrange page's *Take … off* buttons carry a translated widget name, and
  sat in a group that was not allowed to give way: in Greek it pushed the page 64 pixels past
  the edge of a phone, in German 31 — and in English 8, on CI's fonts, which is how it was
  found. The interview list's outcome buttons did the same by 143 pixels in Greek, the account
  page's buttons in Dutch, French and German, and four list headings in Dutch. Those groups now
  wrap, and the words beside them claim twelve rem before anything may sit next to them, so on
  a phone the buttons go underneath.

  Where the buttons *did* fit, the same layout failed quietly, with nothing to scroll: the
  words were left whatever was over. The arrange page had a column one word wide, a career
  entry had fourteen pixels in Greek, and each built-in plugin's description had six in
  English, with the longest word written across whatever was beside it. The browser suite now
  walks every page in Greek and German as well as English, and fails on words that run out of
  their own box as well as on a page that scrolls.

  Walking in Greek found two more. The recovery page's table had a hidden *Actions* label that
  escaped its scroll box and dragged the page sideways, because the report and that page had
  been given a bare `overflow-x-auto` rather than the positioned `scroll-x` #113 made for
  exactly this; the template lint now refuses the bare one. And a heading may break a word too
  long for a phone — *Wiederherstellungslink* is wider than one — rather than run off its edge.

  The *Suggestions* widget, which draws its own heading, had no name anywhere else: the arrange
  page showed its key, its button read "Take  off", and its four arrows told a screen reader
  "Move  up a row". Every widget now has a name in words, and registering one without it is
  refused. (#165)

- **The CV page and the letter page open again.** Making a rendered document point at
  whatever produced it took `related_name="renders"` with the two columns it replaced, and
  both detail pages ask for exactly that — so anyone opening one got a server error instead
  of their CV. Nothing caught it, because the suite tested what the new link *stores* and
  never opened the page that reads it back.

  The reverse is a query rather than a `GenericRelation`, and that is not a detail. A
  relation would give the name back and a cascade with it, and the cascade is the one thing
  that must not happen here: deleting a CV has to leave the PDF an employer received exactly
  where it is, which was the whole reason `RenderedDocument` exists. (#130)

### 🐛 Fixed

- **A test could be handed a translated string because of which test ran before it.**
  `LocaleMiddleware` activates a language per request and nothing deactivated it afterwards,
  so a test that signed in as somebody reading Postulo in Portuguese left Portuguese active
  for every test that followed. It showed up as a test asserting an English message and
  getting a Portuguese one — passing alone, failing in company, and failing differently
  depending on the order, which is the worst shape a failure can have. Every test now starts
  in the instance's own language. Found while adding the portfolio tests, which is to say by
  accident. (#133)

- **A team nobody had been recorded at did not survive an export.** A department travelled
  only as a name beside a contact, so a team with no contact simply vanished from the
  archive — and *a team you applied to before you knew anybody there* is exactly the ordinary
  case the model was written for. Departments are now records of their own in the file. Found
  by the new attachment failing to restore, which is the argument for the round-trip test
  being a round trip. (#138)

- **A stored column width did nothing on a real deployment.** It shipped as a `style`
  attribute on the header cell, and the policy Postulo serves is `style-src 'self'`, which
  refuses one as firmly as it refuses an inline script — so the browser dropped it, the
  column sized itself, and the preference appeared not to save. Development never saw it
  because the strict policy is production's: the browser test that covers widths runs under
  the development settings, and the test that runs under the real policy visited no page
  with a stored width. Both were true and neither could catch it, which is the gap that has
  been closed alongside the width. The script that owns the handle applies it through the
  DOM now, which the policy does not govern, and which is the coherent place for it — a
  width is a pointer gesture, so it belongs to the script that provides the gesture. (#136)

### 🔧 Changed

- **Every API Postulo has is in the wiki, and a test says when one is not.** *The API* named
  every call but kept the scope of each in prose, and the three addresses that answer machines
  about the instance — `/healthz`, `/metrics`, `/logs` — were rows in *Configuration* that
  never said what they return. They have *Health, metrics and logs* now, down to each metric.
  The plugin guide was the core repository's `docs/PLUGINS.md`, not the wiki, and it never
  named the interface each kind of plugin satisfies; it is *Writing a plugin* in the wiki, with
  every kind, its entry-point group and its interface, and every name `postulo.plugins.api`
  promises. `docs/PLUGINS.md` stays as a pointer, because every plugin's metadata links to
  it. `tests/test_wiki_surface.py` reads the wiki beside the checkout against the code, both
  ways, so a call, a metric or a promised name without its line fails. (#170)

- **The wiki is written where it is read.** Its pages lived in this repository's `wiki/` and
  reached the Forgejo wiki only when somebody ran `scripts/publish-wiki.sh`. By the time
  anybody looked, four pages — *Accessibility*, *Hardening*, *Listings*, *Reports* — had
  never arrived, the other seventeen were five days old, and the images had never been
  copied at all, so the wiki's front page had shown its logo broken since the day it was
  added. The pages now live only in the `postulo.wiki` repository, the script and the copy
  are gone, and `CONTRIBUTING.md` says how a page is written there. (#169)

- **The dashboard is a grid of four columns, and a widget can be dragged into place.**
  The last of three: #123 settled where an arrangement is stored, #124 settled how a widget
  is moved by somebody not using a mouse, and this is the grid itself and the gesture.

  **Four columns rather than five**, because four is the count in which the three widths
  every widget already declares — a quarter, a half, a whole row — mean what they say. Five
  has no half; a half-width widget in five columns is two columns or three, and every widget
  would have had to be re-measured against a grid that divides by nothing.

  **A widget has a width and a place in the order; the row falls out of the two.** That is
  the whole model, and the reason it is worth stating is what it makes impossible: there is
  no way to leave a hole in the middle of the page, no way to put two widgets in one cell,
  and so no validating, no repairing, and no answer needed for what happens when a plugin is
  uninstalled and its widget goes — the rest close up. A coordinate model would have needed
  all three.

  Dragging is added on top of the four arrows and does not replace them, which is the rule
  this project has followed since the board learnt to drag: drag and drop fires on neither a
  touch screen nor a keyboard. A drop posts to the same address the arrows post to and gets
  the same sentence back saying which row and place it landed in, so a page arranged by
  dragging and a page arranged by pressing arrows are the same page, saved the same way.
  Nothing is draggable until the script makes it so, because an affordance that does nothing
  is worse than none.

  Widths stayed out of the person's hands on purpose. A width is the widget's own statement
  about how much room it needs to be legible — a six-stage funnel is unreadable in a quarter
  of a row — and what is being arranged is the order. A narrow screen gets one column, read
  downwards; #73 is where the phone gets its own attention and this does not assume it
  solved.

  The "internal widgets plugin" the issue asked for is the registry that already exists,
  named as one. Making a widget a plugin *kind* would put seventeen rows in *Server settings
  → Plugins* for an administrator to switch off, and would need an entry-point contract
  before anybody outside has asked for one; the part of that contract that actually matters
  — a key that cannot collide with another provider's — shipped with #123. (#125)

- **A column header clicks back to no sort at all, and a column can be dragged wider.**
  Two of the four features Dispatcharr's channel table had and Postulo's did not; the other
  two arrived with their prerequisites — selection with bulk actions, and editing in the
  cell — which is why these two were left until last rather than done first.

  Sorting was two-state and never returned to the table's own order, so there was no way to
  undo a sort except by editing the address. It cycles through three now. A column that *is*
  the table's default sort keeps two, because there is nothing to go back to and a third
  click that changes nothing is worse than two honest states. And since the third state
  takes the arrow away, every header now says in words what clicking it would do.

  **A width is a preference, so it lives where preferences live.** Beside which columns show
  and how many rows a page holds, on the profile, following the person to every device
  rather than cluttering every link — which is the line this project already drew between a
  question and a preference, and the one thing from that table that was deliberately *not*
  adopted: theirs keeps the query in session storage and the sort in memory, so a reload
  loses the sort and a filtered view cannot be sent to anybody.

  The handle is the one place a script is unavoidable, since a width is a pointer gesture.
  It is still an addition rather than a replacement: without a script no handle exists at
  all and the columns size themselves exactly as before. And it is not pointer-only — the
  handle is a button, the arrow keys widen and narrow it, and *Home* lets the column size
  itself again, because a column dragged too narrow once must not be too narrow for ever.

  Row reordering was not taken and is not meant to be: a channel list has an order somebody
  chose, a company list has an order somebody *sorted*, and dragging a row in a sorted table
  means either abandoning the sort or lying about it. (#136)

- **The two halves of choosing a company's sector meet, and the consequences are handled.**
  The list is NACE Rev. 2.1 and the picker is chips; what was left was everything that
  follows from a vocabulary that can now be long.

  The companies table draws industries as labels rather than a comma-separated run, showing
  four and counting the rest — a bank that is also an insurer and a software house is three,
  a conglomerate is more, and the cell is already narrow. Filtering stays a box you type in
  rather than a menu, which is the same control at thirty-two names and at three hundred and
  the only one that stays usable at both.

  **Companies → Industries** gains a search that matches a name or a NACE code, shows each
  code beside the person's own word for it, and says *Edit or merge* rather than *Edit*,
  because merging is exactly the tool somebody needs after picking from a standard list
  beside their own words. Only what has actually been given to a company appears there — the
  classification is a list of suggestions, not a list of rows, which is what made it safe to
  make the suggestions long.

  Nothing renames a word somebody wrote themselves, and a company in three fields still
  counts in three. (#141)

### ✨ Added

- **A company's name can be changed where it sits, and a refusal has somewhere to go.**
  Every edit in Postulo was a page — fine for a company nobody looks at twice, tiring for a
  list of forty where the thing wanted is one word in one cell.

  **The interesting half is the refusal.** A form has somewhere to put one: under a labelled
  field, in a form with a heading, which is the arrangement `aria-describedby` and
  `aria-invalid` were wired for. A cell four columns wide has nowhere — under it breaks the
  row, a toast is gone before a screen reader reaches it, a tooltip is never announced. So
  the answer, decided once rather than improvised per column: while it is being edited the
  cell *is* a form, and the refusal goes exactly where every other refusal in Postulo goes.
  That turned out to need almost nothing new — `partials/field_feedback.html` already
  carries `role="alert"`, with a comment saying the role earns its place *through htmx*,
  because an error inserted into a live page is what it is for. A cell arriving through a
  swap is precisely that.

  **Every editable cell posts to the form the page already uses**, narrowed to one field, so
  a cell refuses exactly what the page refuses in exactly the same words. A cell that wrote
  the field directly would be a second way of saving to keep in step with the first, and the
  day it drifted would be the day something was saved without its rules.

  **A column that cannot be edited cannot be edited by address either.** `Column.editable`
  sits beside `sort` and `filter` in the table definition, and the view checks it — a count
  is not editable because it is a count, a date read off a posting belongs to the posting,
  and a status is not offered here at all because it goes through a service that writes a
  timeline entry.

  **Two tabs, both editing, is answered rather than ignored**: the editor carries the row's
  timestamp and a save whose stamp has moved is refused with what the row says now. Focus
  follows the edit and comes back to the value afterwards; Escape abandons; and with no
  script the cell is a link to the form, which is exactly what it was before. (#135)

### 🔧 Changed

- **Industries and tags are chosen as labels now, not as tick boxes.** Industries were a row
  of checkboxes; tags were Django's default scrolling box you ctrl-click. Neither was a
  label, and nothing in Postulo drew a chip — so this is a new control rather than a
  restyling of an old one, and it is written once and used in both places.

  **Layered, never substituted.** The checkboxes, the multiple select and the box for a name
  that does not exist yet are all still in the page, still submitting; the chips are drawn
  over the top and those are hidden. With the script blocked the forms are exactly what they
  were, which is the rule the board's dragging already follows.

  **A name that is new looks new before anything is saved** — outlined rather than filled —
  because otherwise people create *Fintech*, *FinTech* and *fintech* and find out afterwards
  that the slug collapsed them. Tags gained the same "add one that does not exist yet" that
  industries always had, matched by slug, and a tag made this way keeps no colour: colours
  are chosen on the tags page, where there is room to see them beside each other.

  **The keyboard vocabulary is a decision, and one of its conventions is deliberately not
  followed.** Enter commits what was typed rather than submitting the form; Escape abandons
  it; the arrows walk between labels, mapped through the reading direction; every remove
  button carries the name inside it, so eight of them are eight different announcements
  rather than eight identical ones; and adding or removing one says so in a live region.
  **Backspace does not delete the last label** — it is the convention, and it is also a way
  to delete something by pressing the key you press to correct a typo, in a control whose
  values are somebody's own words.

  The remove button is 24 by 24 with room around it, which SC 2.5.8 has caught this project
  over twice already, and the × sits at the end edge rather than the right. (#139)

- **A widget can be placed in two dimensions without a mouse.** *Move up* and *move down*
  are a complete vocabulary for a list and not for a grid, and `app.js` states the rule that
  makes this a prerequisite rather than a refinement: drag and drop does not fire on touch
  screens and is not reachable from a keyboard, so it is an addition to the control that
  works everywhere, never a replacement for it. The grid cannot ship until the control
  exists.

  **The dashboard is a flow rather than a matrix, and that chose the mechanism.** Widgets
  have widths and fill rows in order, so there is no cell to name — which rules out a
  row-and-column picker, and rules out a "move this one, then choose a destination" mode
  that would need two interactions and state between them to work with scripts off. What is
  left is four directions over the order: *left* and *right* move one place, *up* and *down*
  move a whole row. On a narrow screen there is one column and the two axes are the same
  move, which is what *up* means when there is only one column.

  Every one of them is still a form that posts. An arrow that cannot act is disabled rather
  than absent, so the cluster keeps its shape and the arrow somebody reaches for is where it
  was last time; the redirect carries a fragment so focus lands on the widget that moved;
  and a message says which row and place it landed in, because a move that happens in
  silence is a move somebody using a screen reader has to go looking for. Two new arrows
  joined the icon set, and the stylesheet already mirrors that pair for right-to-left. (#124)

- **Every account owns its dashboard arrangement from the day the account exists.** Nothing
  was ever shared between accounts — two people who had never arranged anything were looking
  at the same *list of keys*, each computed against their own records — but the arrangement
  itself belonged to nobody until somebody touched the setting. It is stored now, from the
  moment the profile is, which is what a grid needs before a widget can be dragged into
  anything.

  **The null that meant *never arranged* was load-bearing, and what it carried is now
  written down.** It made a widget added in a later release appear for anybody who had never
  arranged their dashboard. With every account holding a list, nobody is ever "never
  arranged" — so the rule moves to a seen set: every key an account has already decided
  about, and a key in neither list is new *to that account*. That is the one of the three
  candidates that also works when the new widget arrived with a plugin installed on a
  Tuesday rather than with a release, which a generation marker could not.

  **The trade is made knowingly.** A new widget no longer walks onto a page by itself; it
  waits on the arrange page under *New*, with the dashboard naming what is waiting, and
  saying no is an answer that is remembered. Strictly that is one fewer thing happening
  without being asked — the old behaviour changed somebody's page during an upgrade.

  **And the key namespace is decided while it is free**: a bare key is Postulo's, anybody
  else's is `provider:key`, and registering the wrong shape is an error at start-up. A key
  lands inside every stored arrangement, so a collision found after people have arranged
  their dashboards is a data migration of every one of them. The arrangement travels in the
  archive now too (format 12), and the seen set with it — without that, a restore would
  announce every widget in Postulo as new to somebody who has been reading their own
  dashboard for a year. (#123)

### ✨ Added

- **The list of areas of activity is a classification now, not thirty-two names somebody
  wrote down.** The old list had *Gaming* and *E-commerce* and no *Mining*, no *Water
  supply*, no *Arts*, and nothing at all for a third of the economy. The fix is not more
  names: a hand-made list of two hundred would be two hundred names in thirty-nine
  languages, carried by this project for ever.

  So the suggestions are seeded from **NACE Rev. 2.1**, the European Union's statistical
  classification of economic activities, at **division** level — 87 two-digit codes under
  22 sections. That depth is a choice between three, not the file that was easiest to parse:
  its 21 sections are coarser than the hand-made list ever was (*Information and
  communication* was one word for software, telecoms, publishing and film), and its 615
  classes are a form nobody fills in.

  **The translations are the argument more than the taxonomy is.** Eurostat publishes NACE
  in all 24 official EU languages, so 87 × 24 names arrive already written by the body that
  maintains them, and not one of them is a string this project translates — they are
  reference data in the same sense that a language's own name is. Where the EU publishes no
  name, the English one stands: inventing NACE names for Catalan or Ukrainian would be
  Postulo asserting a classification it does not maintain.

  **It is a seed and never a closed list, because NACE classifies the business rather than
  the job.** Somebody applying to a bank's software team is applying to *Financial and
  insurance activities*, which is true of the employer and useless to the applicant. So the
  vocabulary stays one set per person in their own words, *Fintech* remains a perfectly good
  industry, and the short familiar names Postulo always offered stay at the top of the list
  — those are what people type. A name that happens to be a division quietly carries its
  code, which is what makes a report legible to an employment office that thinks in NACE.

  **The licence was checked rather than assumed.** Reuse of Commission documents is
  authorised under Decision 2011/833/EU, and the default licence for Commission-owned
  content is CC BY 4.0 — attribution required, changes to be indicated. Both travel with the
  data in `jobs/data/LICENCE.md`, which also says what was changed and how to replace the
  file when Rev. 3 arrives. The revision is recorded inside the data rather than in a
  variable name, because a list copied into a Python module gets copied again.

  Nothing anybody already had was renamed, merged or given a code retroactively: the
  migration adds a column, widens a name that was capped at sixty characters, and fills in
  nothing. (#140)

- **One registry of external identifiers, with a matrix saying which each one identifies.**
  The separation asked for already existed and was the least interesting part: a company's
  scheme could not appear on a person because of which module a form imported its choices
  from. That is a convention, and a convention holds until somebody wires a form up
  differently. It is a guarantee now — a scheme says which subjects it identifies, and a row
  that disagrees is refused at the model by every route in, `objects.create` included.

  **What the separation cost is the interesting part.** The two sets were not disjoint.
  ISNI identifies contributors *and* organisations by its own definition; Wikidata has items
  for both; LinkedIn has company pages and personal profiles alike. Two files that never had
  to agree had quietly picked a side for each, so a researcher could not record their
  Wikidata item and a university could not record its ISNI — which is exactly the identifier
  an EU application form asks an institution for. Nobody had filed a bug about either; both
  simply followed from the shape.

  One scheme identifying two subjects also means one scheme needing two addresses, which two
  registries could never have expressed: a LinkedIn company page and a personal profile are
  different links, and both are now right.

  **A new plugin kind, and one that governs nothing.** Every other kind answers *is this on
  for this person*; a registry answers *what does this key mean*, so switching it off would
  leave every stored identifier without a label, a link or a check. It is ungoverned
  alongside transports, and internal-only — enforced rather than intended, since the kind
  advertises no entry-point group at all. A plugin per national company register is the
  obvious next thing (`register` is one generic scheme for SIRET, NIF, Companies House, KvK
  and Handelsregister alike), but a third-party contract is a promise about breakage and
  that one is not written yet.

  **It can be a plugin because a scheme owns no rows.** The two identifier tables stay in
  core, owned and migrated by core; what the plugin contributes is vocabulary. That is the
  difference from the contact-details half of #100, which needed a plugin to own a table.

  No validation got weaker: every pattern and both checksums came across, and the existing
  tests pass unchanged rather than adjusted to fit. No key changed, so no row moved. (#109)

- **A career entry can say the same thing in more than one language.** At the document level
  this already worked: a CV declares what it is written in, and the PDF is hyphenated,
  justified and laid out for that rather than for whoever made it. One level down it did not.
  The career record held one text per field, so a CV declaring `fr-fr` printed exactly the
  same English job titles as one declaring `en-gb` — and the honest way to keep a CV in two
  languages was to keep two careers, typed twice, drifting apart the moment a date changed.

  **A second language is now a translation rather than a second record.** One row per entry,
  per language, per field, and the entry itself is untouched: correcting an employer or a
  date on the master copy corrects it in every language at once, which is the whole reason
  there is a master copy. The database holds the rule that a JSON blob on the entry could
  not have — one text per field per language, decided there rather than by whichever save
  happened to be last.

  **Which fields may be said differently is a decision per field, not a mechanism applied
  uniformly.** An employer's name and an institution's are not translated: *Universidade de
  Lisboa* stays that on an English CV, and rendering it otherwise invents an employer who
  never existed. Certifications translate nothing at all, because a credential's name and
  the body that issued it are that body's wording. What is offered is either the person's
  own words or something that genuinely differs between languages — a job title, a city, a
  qualification, a grade on a scale that does not exist elsewhere. Offering the box is what
  would have caused the harm, so the boxes that would have caused it are not there.

  **Falling back is visible, or it is a trap.** An entry with nothing in the CV's language
  prints its original, because a blank line where a job used to be is worse than a line in
  the wrong language. The CV's own page then lists which entries did that, with a link to
  each, *before* the export button — the same rule themes settled one issue earlier, for the
  same reason. Discovering it in the PDF an employer already has is not discovering it.

  So the profile gains one small field: which language the career record itself is written
  in. Without it, a CV in the record's own language would report every entry as untranslated
  and the warning would be read once and ignored thereafter.

  Every theme renders this without knowing it exists — a plugin's template written against
  `entry.item.role` gets the French job title and could not have been asked to do anything
  else. The archive carries the translations (format 11) and brings them back. (#131)

### 🔧 Changed

- **A theme says which kinds of document it sets, and nothing offers a pairing it cannot
  produce.** A theme used to be two things at once: two choices frozen into the model, and a
  directory holding one template per kind. Two themes and two kinds is four templates; five
  kinds is ten, and a theme that had never been taught a kind resolved to a path that did not
  exist — a missing-template traceback at the moment somebody pressed *Export PDF*, which is
  the worst possible time to find out.

  **A theme declares what it sets by having a template for it**, so the two cannot drift
  apart, and the picker on each form offers only the themes that set that kind. Falling back
  to `plain` instead would have put somebody's Classic CV beside a plain portfolio in one
  envelope, and the pair would not have looked like one person's application — the same bad
  outcome, said too late to do anything about. Refusing is not the harsher answer here; it is
  the same answer, in time.

  **A theme name nothing recognises is a different question and gets the opposite answer.** A
  theme that cannot set this kind is a live choice somebody could still make. A theme left
  behind by a plugin that was uninstalled is a row remembering something that has gone, and
  refusing there would mean removing a plugin had quietly taken somebody's CV with it. So
  that one falls back and still exports.

  **`Theme` stops being `TextChoices`, because choices are a migration boundary.** They are
  written into every migration that touches the field, so a theme arriving from an installed
  plugin could never have been one of them. The column is a validated name now and every
  existing row keeps its value; the validator travels with the column rather than living only
  in the form, because the form is one of several doors.

  **Themes may now arrive from an installed plugin, and from nowhere else.** A plugin
  declares them and ships a `templates/` directory beside its package, exactly as it already
  ships `locale/`; registering the plugin puts that directory on Django's search path,
  appended, so a plugin can add a page of markup but never replace one of Postulo's. There is
  no upload form for themes and there will not be one: rendering executes the template, so an
  uploadable theme is remote code execution with a file picker on it. A plugin's markup runs
  because an administrator installed the plugin, having read its author, licence and source —
  and the picker says so, naming a plugin's theme with whoever provides it, because the menu
  is the only place a person meets a theme.

  `Theme` and `ThemeKind` join the plugin surface, which settles one of the two questions
  `postulo.plugins.api` had listed as open. Honouring the document's language and direction
  is part of the contract a theme takes on, stated rather than hoped for, and each of
  Postulo's own is held to it by a test that renders an Arabic CV and looks for the
  direction. (#132)

- **A rendered document points at whatever made it, rather than at one of two columns.** The
  documents app already held two authored kinds, and the plumbing around them named both:
  `RenderedDocument` had a `cv` and a `cover_letter`, `DocumentCopy` had a `rendered` and an
  `upload`, and `archiving.py` asked `isinstance` for what the columns could not say. A
  portfolio, an email or a report would each have been two more nullable columns on two
  models, a branch at every reader, and a migration.

  `CVItem` decided this the other way one model over, and its docstring is the argument: six
  nullable foreign keys with a check constraint say the same thing less clearly and need
  widening every time a kind is added. Both links are generic now, so the next kind of
  document is a package.

  **The two `on_delete` behaviours are not the same one, and a generic link has neither, so
  both are written out.** Deleting a CV must *not* delete the PDF an employer received — that
  is the whole point of the model — so a receiver clears the link, which is the `SET_NULL`
  the column used to carry. Deleting that PDF *must* delete the rows saying where its copies
  went, so the cascade lives on a `GenericRelation` at each document. Getting either
  backwards would lose somebody's record of what they sent, and each direction is a test.

  The archive carries a kind and a local id instead of two columns (format 10), and the
  importer reads both shapes. Listing documents with where their copies went is still two
  queries rather than one per row — a generic link has no join to follow, so the batch
  lookup is what answers for it. (#130)

- **Choosing a language is one control now, and it says how each translation was made.**
  Thirty-nine radio rows sat beside a time zone field that is a single line, and the list
  will only get longer. It is a disclosure: closed it shows the language in use — flag, name
  and state — and open it is the same rows as before.

  **Not a `<select>`, and that is not a shortcut taken.** An `<option>` holds text and
  nothing else. It takes `lang` on itself, so a dropdown can say *this option is Greek* and
  no more: a flag inside the option text is read out beside a name that already says what it
  is, a symbol saying how the translation was made would be announced as part of a string
  claiming to be Greek while being neither Greek nor a word, and there is nowhere at all for
  the percentage a partly-translated language shows. The request asked for a flag, a name and
  a symbol in one control — which is exactly the combination an `<option>` cannot hold.

  So the *shape* is what changed. Each language keeps its own `lang`, the flag stays hidden
  from screen readers, and everything about the state of a translation now sits outside that
  span in the interface language — including the percentage, which used to be appended to the
  language's own name for want of anywhere else to put it.

  **Every symbol is accompanied by words.** A glyph alone means nothing to somebody who
  cannot see it and is a guess for anybody who has not learnt it, so each has a phrase read
  out beside it and the legend is on the page rather than in a tooltip a keyboard cannot
  reach. And the words are *written, not yet read by a speaker* rather than anything about
  machines: `pt-BR` was seeded from `pt-PT` and adapted by hand, and what is true of every
  language in that group is that no speaker has read it.

  **The keyboard is the cost of not using a dropdown, and it is paid.** Arrow keys come from
  the radios themselves; Home, End, type-ahead on each language's own name, Escape and
  returning the focus are given back in a few lines. None of it is needed for the control to
  work — the disclosure opens and the radios submit with scripts off entirely. The browser
  suite walks it with a keyboard. (#119)

- **The image now contains what it runs, rather than everything used to build it.** The
  Python side of the build was a single stage, so every intermediate was already sealed into
  a layer by the time anything could remove it — a later `RUN rm` frees nothing, because the
  bytes are below. That is how 296 MB of uv's download cache shipped, and it is why 20 MB of
  `.po` source catalogues shipped too: Django reads the compiled `.mo`, and nothing at run
  time has ever opened a `.po`.

  Building the environment and compiling the catalogues in a stage of their own makes the
  deletion real rather than decorative, and makes the same true of whatever the build needs
  next: what is not copied forward is not there, with no flag for anybody to remember. The
  extra-packages step gets simpler as a side effect — git is left behind with the stage
  instead of being purged — and `collectstatic` runs as plain `python`, so the lock and the
  manifest no longer have to be present in the shipping image purely to satisfy `uv run`.

  **Two savings are deliberately not taken, and the reasons are beside them.** uv stays: 45 MB
  of binary, and dropping it would not break installing a plugin through the interface but
  would make it slower and hand an operator a resolver this project does not test with.
  Django admin's collected assets stay: the admin's URL is read at run time and
  `collectstatic` runs at build time, so making it a build argument would hand somebody an
  image whose admin page loses its stylesheet the day they enable it. Neither is worth what
  it saves. (#157)

- **Every plugin Postulo ships is now its own package, and a test says so.** They were
  scattered through the applications they happened to be useful to — the notifier and the
  transport in `notifications`, the store in `documents`, the importer in `resume`, and the
  telephone-numbers feature written entirely inside `core` a week after the plugin
  documentation said not to. Each is now `postulo/plugins/<name>/`, with its manifest, its
  catalogues and its code in one place, exactly like a plugin somebody else writes.

  **The measure was never that the directories moved.** It is that the next one cannot be
  written in the wrong place without something failing, so the test takes its list from the
  registry rather than from a list in the test: a built-in added tomorrow is checked
  tomorrow, including the one nobody remembers to add to a list. It fails on a plugin
  outside `postulo.plugins`, one with no catalogues of its own, and one that imports
  anything from Postulo but the declared surface without a written reason.

  **Nothing Postulo ships touches the database any more**, which is the part worth keeping.
  Getting there meant drawing two lines the code had already argued for: the career record
  an importer fills in is Postulo's shape rather than Europass's — so Postulo defines it,
  every importer fills the same one, and writing it moved to `resume.importing` — and the
  store contract a plugin author writes against joined `postulo.plugins.api`, so the local
  store imports it from where everybody else does. Ownership scoping done wrong in a plugin
  is how one person sees another's data; the way not to get it wrong in seven places is not
  to need it in seven places.

  Twenty-seven strings moved into six new catalogues, each keeping the translation it
  already had in all thirty-nine European languages. Four dependencies on core disappeared
  outright; the five that remain are recorded with the reason each is a reason to depend on
  Postulo rather than a failure of discipline. Built-ins still register through
  `register_builtin()` rather than an entry point, deliberately: an entry point would be a
  slower import of a module in the same distribution, and would cost the documented
  ordering that lets a third-party plugin take precedence over Postulo's own. (#129)

- **A plugin's state is drawn around its checkbox, not only inside it.** *Settings →
  Plugins* is a column of rows, and reading which of a dozen are on meant looking at each
  13-pixel tick in turn. A ring of colour — green around a ticked box, red around one that
  is not — makes the column readable in one pass.

  It says nothing the checkbox was not already saying. The tick is what a screen reader
  announces and what a keyboard toggles, so the colour is redundant by design and WCAG
  1.4.1 never comes into it; this would be a different change if the tick had been replaced
  by a colour. `box-shadow` rather than `outline`, because `:focus-visible` owns the outline
  throughout this stylesheet and the two would otherwise fight — a keyboard user tabbing
  down the list would lose the focus ring on every row. Shadows and outlines paint
  separately, so both show at once.

  **A row an administrator decided keeps the colour and loses the halo.** A full-strength
  red ring around a control that is not yours to change reads as a fault you are being
  blamed for, which is the opposite of what that row is there to say. Both themes are
  defined rather than left to whichever ground the glow lands on. (#122)

- **Reading a Europass CV is a plugin now.** The reader was already shaped like one — it
  decides which of the two formats it has and dispatches, which is the same split sources
  have used since the beginning — so this mostly says so out loud: a new `importer` kind, and
  Europass registered into it the way the built-in notifier and document store already are.
  The import page asks the registry what this instance can read instead of naming Europass,
  which is the whole point: the next format somebody wants is a plugin rather than a patch to
  a view. **One importer, two formats**, because that is what the code is — both readers
  produce the same record and `apply()` is shared, so two plugins would be one thing
  described twice. On the way, the refusals that keep a hostile file away from a parser moved
  from Europass to the kind: an empty file, anything over the cap, and **a `DOCTYPE` in
  anything that looks like XML**, which is where entity expansion lives. Those are a threat
  every importer faces rather than one Europass happened to think about, and "every plugin
  author remembers" is not a control. `apply()` stays in core — an importer turns bytes into
  a record and never touches the database, which is what keeps one from needing ownership
  scoping of its own. (#99)

- **`TRADEMARKS.md` says what the licence cannot.** The code is AGPL; the name and the logo
  are not, and until now nothing said so — a reader had no way to know what a fork may call
  itself. That matters here more than in most projects, because the README makes four
  promises and a licence cannot enforce a promise: somebody could fork this, put a feature
  behind payment, keep the name, and everybody who had heard *never paywalled* would have
  been told something untrue. Reserving the name is the only instrument that speaks to that,
  and it is AGPL-3.0 §7(e)'s own provision rather than a restriction bolted onto a free
  licence. The document leads with what needs no permission, because that is nearly
  everything: run it, fork it, redistribute it unmodified under its name, say truthfully
  that your thing works with it, **name a plugin `postulo-something`** — eight repositories
  already do, and a note that put them in the wrong would cause exactly the harm it was
  written to prevent — and call your instance whatever you like. Another name is asked for
  in one case: a materially modified fork that somebody could install thinking it was this.
  It also closes a real gap rather than a hypothetical one. **`assets/support/buy-me-a-coffee.png`
  has been in the tree since the support banner landed, with nothing beside it saying whose
  mark it is**; it now carries a notice, as the flags already do. Lucide and flag-icons were
  fine — those are copyright licences and were satisfied. A mark is not licensed at all,
  which is why it needed a different kind of note. Nine tests hold the document to the
  artwork actually in the tree, so vendoring something new without a line for it fails.
  (#107)

- **`FUNDING.md` says what the funding link means.** `.github/FUNDING.yml` held one URL,
  and a URL cannot say what is being asked for, what it changes, or — the part that matters
  — what it does not. So the companion says it: that nothing is ever paywalled and no
  feature will be held back and sold separately; that **money buys no influence**, moves no
  issue up the list and does not outweigh a good bug report from somebody who has given
  nothing, because a project that sells priority has quietly become a product with
  customers; what support actually pays for, in categories rather than amounts, since
  publishing a figure implies a threshold and a threshold implies something happens when it
  is missed; and what is worth as much from somebody with no money — which, for a great many
  people looking for work, is the honest position. It ends by telling anyone who would
  rather give to a person in worse need to do exactly that. (#89)

- **Export has left the account menu.** It is a settings section of its own — *Settings →
  Your data*, beside deleting the account — so the header was a second permanent route to
  the same page, in a menu of five items where every other entry went somewhere different.
  A menu with two ways to one place is a menu people learn to stop reading. Nothing is
  harder to reach: the settings section is the home, the **delete-account page still offers
  the archive** (leaving without your data should never be the easy path, and that is the
  moment it matters), and the dashboard's *Shortcuts* widget keeps its link for anybody who
  put it there — which is not the same as the application putting it in everybody's header.
  Taking data out remains one of the four commitments; what upholds it is that the export is
  complete, documented and one button, not the number of links pointing at it. (#86)

### 🐛 Fixed

- **Three plugin descriptions were never translatable, and nobody could have noticed.**
  `scripts/messages.py` reads the source rather than importing it, so it knows a translation
  call by the name at the call site — and three plugins import `gettext_lazy as _lazy`, a
  name it had never been told about. Their descriptions were therefore never extracted, so
  the catalogues were complete and the strings were simply not in them: the one shape of
  translation bug that a completeness check cannot see. The extractor knows the alias now, a
  test fails on the next one somebody invents, and the strings are translated into all
  thirty-nine European languages. (#149)

- **The browser suite no longer fails whichever test happens to run after the fortieth
  sign-in.** Every one of them signs in, all from one address, and the limit that stops a
  stranger guessing passwords cannot tell a test suite from an attacker — correctly. So the
  suite got a `429` somewhere in the middle once it grew past the allowance, and *which* test
  got it depended on how many had run before, which is the worst shape a failure can have.
  The limiter is now emptied before each browser test rather than switched off, so it is
  still the real one and the security tests still hold it to its numbers. (#119)

- **Rebuilding the stylesheet no longer produces a diff nothing can read.** `app.css` is a
  build artefact that is committed on purpose — Postulo runs without Node — and Tailwind was
  writing it minified, as one line of 76 kB. So every rebuild produced a two-line diff a
  hundred kilobytes wide: a terminal wrapped it into thousands of rows, a review tool
  truncated it, a pager could stall on it, and `git add -p` was unusable. Reported from a
  terminal that stopped part way through one.

  It is written out now. That is not only about being able to scroll past it: a Tailwind
  upgrade quietly changing a base rule is exactly the sort of thing a diff should catch, and
  nobody could see one. The cost was measured rather than guessed — 819 bytes of the 11 kB
  WhiteNoise actually sends, because compression removes almost everything minification does
  — and a stylesheet a person can reason about is worth eight per cent of one response.

  The image builds it with the same command, so its copy and the committed one stay byte for
  byte identical, and CI still fails on a stale one. Git is told the file is generated, so a
  forge collapses it by default — but it stays diffable, because the diff is now worth
  reading. (#159)

- **Port 465 could not be configured at all, so half the mail providers there are could
  not be used.** There are two ways of putting TLS on an SMTP session. STARTTLS connects in
  the clear and asks the server to upgrade the socket, which is ports 587 and 25; implicit
  TLS hands over a certificate before a byte of SMTP is spoken, which is port 465. Postulo
  did only the first — no `SMTP_SSL`, no `use_ssl`, nowhere — so an administrator entering
  their provider's documented settings got a socket waiting for a greeting the server would
  never send, and, ten seconds later, the word `timed out`. Ticking the STARTTLS box changed
  nothing, because the failure happened before STARTTLS would have been reached.

  Found configuring a real provider on a real instance, which is the only way this was ever
  going to be found: every test in the suite that touches mail either mocks the socket or
  sends to a local memory backend.

  **One control with three states, not a second checkbox.** Django's SMTP backend raises when
  `use_tls` and `use_ssl` are both set, and rightly — they are alternatives, not layers — so
  a checkbox each would have offered a pair that cannot be saved. *Connection security* is one
  field and cannot express the invalid combination. A migration carries the old boolean into
  it in the order add-carry-remove, because the schema change on its own would have dropped
  the column first and quietly reset every instance to "nothing chosen"; and
  `POSTULO_EMAIL_USE_TLS` goes on meaning what it means for every `.env` that sets it, with
  the new `POSTULO_EMAIL_SECURITY` winning where both are given.

  **The timeout now says which mistake it is.** Pointing one kind at the other's port is the
  ordinary error rather than an exotic one — the two ports are documented interchangeably by
  half the providers there are — and both directions fail identically. So a failure on 465
  without implicit TLS, or on 587 with it, names that before repeating the original message. A
  port that is neither is left alone: a relay on a port of its own is ordinary for a
  self-hosted instance, and the surest way to make a settings page hated is to argue with what
  was typed into it. For the same reason the default port is filled in only when the box was
  left empty. (#158)

- **The lock protecting people's accounts rested on a route that might deliver nothing.**
  Mail may not be switched off while it is the last way anybody could get back into their
  account. What that rule actually checked was whether a mail transport was *selected* — a
  statement about configuration, not about delivery. An instance whose relay had been
  switched off, whose password had been changed, or whose host no longer resolved still
  counted email as the last way in, and refused every attempt to switch the transport off on
  the strength of a route that delivered nothing. The refusal told an administrator it was
  protecting accounts it was not, in fact, protecting.

  Now a route counts because it delivers. The Email page says when mail last went out, or
  that the last few messages failed and what the transport said about it — recorded from
  what sends actually did, never probed, because the lock is evaluated while rendering a page
  and an answer that opened a connection would make reading a page send traffic. The **Send a
  test message** button goes through the same code a real message does, so nothing extra had
  to be wired up for it to count as evidence.

  **It takes three failures in a row, not one.** A relay that refuses a single address has
  told us about that address rather than about itself, and an instance that has never sent
  anything counts as working. Both fail in the same direction on purpose: this makes the lock
  honest, never eager to open, because a lock that opens on a shrug is worse than one that
  stays shut on an optimistic guess.

  **When it does open, the page says the thing that matters.** Mail failing while it is the
  only route means nobody who forgets a password can get back in — and that is true whether
  the lock is shut or not, because those accounts are stranded by the relay, not by the
  setting. So the page names how many people that is, and the lock opens rather than standing
  between an administrator and the transport that would fix it.

  `recovery_routes()` now returns routes that know the difference between existing and
  delivering, because SMS, an administrator-issued link and a passkey will each have to
  answer "does this one actually work" in its own way. (#152)

- **The runtime image carried 430 MB it never runs, including a Node.js runtime.** Trivy and
  Grype, run against the image on the test instance, found Playwright, pytest,
  pytest-playwright and pytest-base-url installed in the application's own virtual
  environment — 136 MB of browser test tooling, bundling its own Node binary, in a container
  that launches no browser — and 296 MB of uv's download cache left behind beside it. Out of
  906 MB.

  **The Dockerfile read correctly, which is why nobody saw it.** `uv sync --locked --no-dev`
  omits the dependency group called `dev` and nothing else, and `pyproject.toml` declares two
  — `dev` and `e2e` — with both named in `default-groups` so that `uv sync` never quietly
  removes the browser test's dependencies. That is a good reason, and it collided with the
  image build. Checked in the image rather than reasoned about: none of the `dev` group was
  present, all four of `e2e` were. `--no-default-groups` asks for the project and the named
  extra and nothing else, and stays right when a third group is added.

  The cache is the same shape of mistake in a different place. `uv` unpacks every wheel into
  `~/.cache/uv` and keeps it; in a single-stage build that is shipped, and deleting it in a
  later layer would free nothing because the bytes are already below. `--no-cache` was
  already on the plugin install a few lines down, for exactly this reason.

  Three tests read the Dockerfile and the project file rather than the image, because nothing
  here builds an image (#81) and this is the second mistake in that file to reach a
  deployment. (#154)

- **The image could not be built, and had not been buildable since #111 landed.** That
  issue taught the production settings to refuse a secret key shorter than fifty
  characters, which is right, and `docker/Dockerfile` passed `collectstatic` a literal
  twenty-eight characters long. Every `docker build` failed at that step. It was found by
  deploying, which is the expensive way to find it.

  **The reason it landed green is the interesting half.** CI runs the same `collectstatic`
  under the same production settings and passes, because the workflow's key is long enough
  and was written that way for `security.W009` years before this rule existed. What CI does
  not do is build the image: that needs a runner advertising the `docker` label and none is
  registered (#81), so `image.yml` has never run once. The check that would have caught this
  is the check nobody has ever seen execute.

  The build now generates its key and throws it away with the shell that made it. A longer
  literal would have passed the rule while remaining a published constant somebody could
  paste into a `.env`; a random one passes it for the reason the rule exists. `collectstatic`
  needs a key at all only because the settings module insists on one before it will import —
  nothing it writes is signed, and no session, cookie or stored credential exists at build
  time. A test now reads the Dockerfile and every workflow and puts each literal key through
  `refuse_a_weak_key`, so the two cannot drift apart again while the real build stays
  unexercised. (#121)

- **Every form said "I am invalid, and these two elements explain why" — and neither element
  existed.** Django renders a refused field with `aria-invalid="true"` and
  `aria-describedby="<id>_helptext <id>_error"`, which is exactly right, and Postulo's own
  field partial then drew the help and the error **without those ids**. So a screen reader
  announced the invalid state and then had nothing to read, while the message sat on the page
  in red two lines below, reachable to eyes and to nothing else. On a company form with one
  empty name: four references, four of them dangling. It is SC 1.3.1 and SC 3.3.1, both level
  **A**, under the AA the README commits to — and the application already knew how, because
  the pages allauth renders were correct throughout. Half of it honoured the promise and half
  did not, which is worse than a consistent omission: anybody testing the sign-in flow with a
  screen reader would have concluded it was fine.

  The two paragraphs now carry the ids the input already claims, from one partial the whole
  application shares. The id goes on the error *block* rather than on each message, because a
  field with two errors would otherwise emit it twice and `aria-describedby` names it once —
  and a duplicate id resolves to whichever came first, which is the same bug wearing a
  disguise. `role="alert"` earns its place through htmx rather than page loads: most screen
  readers ignore an alert that was already in the document when it arrived, but these forms
  come back through a swap, and an error inserted into a live page is what the role is for.

  **Groups got the same treatment, one layer up.** A set of radios or checkboxes drawn as a
  `<fieldset>` — theme, navigation, industries, token scopes, interview contacts, language —
  had no association at all rather than a broken one: Django deliberately leaves
  `aria-describedby` off a widget it expects to be drawn as a fieldset, because the group is
  what the help and the errors are about, and nothing was putting it on the fieldset. They
  now carry `field.aria_describedby`, which is Django's own computation of the value, so it
  cannot drift from the ids the partial renders.

  Checked by resolving every `aria-describedby` on every page against the document, and by
  submitting five forms empty first — a page that has never been refused has no errors to
  point at, so the half of this that mattered was unreachable by walking pages. axe reports
  none of it: it cannot know that a `<p>` below an input was meant to describe it, and it does
  not report a dangling reference at all. Two more turned up that way, both checkboxes whose
  help was drawn by hand in a `<span>`. (#114)

- **A release run finishes again, and building the image is something you start rather than
  something that hangs.** v0.2.0 was published — wheel, sdist, notes, all of it — with its
  workflow run sitting in `waiting` for ever, because the second job in that file asked for a
  runner advertising the `docker` label and no runner advertises it. Forgejo schedules a job
  **before** it evaluates the `if` that would skip it, so the job was neither skipped nor
  failed: it queued, and the run never finished. The comment at the top of `ci.yml` had
  warned about exactly this — *a label no runner has does not fail the job: it queues it for
  ever, which looks exactly like CI passing until somebody checks* — and it came true one file
  over. Giving `runs-on` an expression moved the symptom without removing it: the job then
  failed in **zero seconds having run no steps**, which is what an unschedulable job looks
  like when it is not left hanging. So the image build now lives in `image.yml` and is started
  by hand, with the tag to build as its input. A workflow nobody starts cannot queue, the
  release workflow has one job, and it always completes. The repository variable that gated
  the old automatic trigger went with it — a switch on something that only happens when you
  press the button is a second way of saying no. Nothing is lost that worked: that job had run
  three times and failed three times, and had never once built an image. (#81)

- **CI had tested nothing for a fortnight, and looked merely red rather than empty.** A test
  imported `config/settings/prod.py` at module scope to read the redirect exemption list from
  what actually ships rather than a retyped copy — a good instinct. But that module refuses to
  import without `POSTULO_SECRET_KEY`, and the only thing supplying one was the **`.env` in
  the developer's own working copy**, which is gitignored and which CI does not have. So the
  import raised there, and because it happened during *collection* it aborted the whole run:
  not one failing test, no tests at all, on three Python versions and in the browser job, on
  every push for fourteen commits. The two jobs that kept passing were the two that never run
  pytest. Nothing distinguished "the suite failed" from "the suite never started", which is
  how it hid behind a red mark people had stopped reading. The file beside it had already
  solved this properly and said why — *the repository's `.env` belongs to whoever is
  developing here* — by reading production in a subprocess with the environment stripped;
  there is now one such reader in `tests/security/conftest.py` and both files use it.
  Reproduced by moving `.env` aside, which is how this should have been checked in the first
  place.

  With the suite running again it immediately caught three things a green local run never
  would. Two were **layout on a machine with different fonts**: the action bar beside a page
  heading did not wrap, so on Linux the buttons were wide enough to push *Applications*
  sideways at 320 pixels — fixed on all thirteen pages that share the pattern rather than the
  one that happened to overflow, because which one does is a question about typefaces. And
  the warning above *Server settings → Plugins* rendered its three paragraphs as three
  94-pixel columns: `.alert` is a flex row so an icon can sit beside the words, every other
  alert passes it a single element, and that one passed three. The third was a **race in a
  test**: setting an image's `src` is synchronous and fetching it is not, so checking that
  the flag had loaded the instant its attribute changed was a race won on the machine it was
  written on and lost on a slower one.

  The last of them took four rounds to find because the test kept answering confidently and
  wrongly. *Server settings → Plugins* scrolled 8 pixels, and every element over the edge was
  inside the settings sidebar's scroll box — which is on every settings page, while only that
  one scrolled. The cause was a **filesystem path in a sentence**: paths have no word
  boundaries, so a browser will not break one, and on Linux the font made it 8 pixels wider
  than the card. A walk over rectangles could never have found it, because a margin, a
  transform and an unbreakable string all add scrollable overflow that
  `getBoundingClientRect` does not show. The test now finds the culprit by hiding elements
  until the page stops scrolling, which looks at no boxes at all and cannot be fooled. (#117)

- **Every page in Postulo scrolled sideways on a phone, and one row of links was most of
  the reason.** At 320 CSS pixels — the width a normal window has at 400% zoom, which is how
  somebody with low vision reads — all thirteen pages measured overflowed by an identical 331
  pixels. Identical is the tell: six navigation links come to 635 pixels, a flex row does not
  care how wide the window is, and that one element was doing it on every page at once.
  **Reflow is level AA**, and the README promises AA without qualification. Below 768 pixels
  the row is now a disclosure, the same `<details>` the account menu beside it has always
  been: it opens with no script, closes with Escape, and the links are written once and
  rendered twice so only one copy is ever in the layout. Wrapping the row instead would have
  been one class and three lines of navigation above every page on a phone.
  **Five more went with it**, none of which anybody had seen, because the navigation was
  hiding all of them: a fixed 288-pixel column in the plugin tables; an action bar of three
  buttons that would not wrap; a grid whose items refused to shrink; the API page's
  `openapi.json` address, which has no word boundary in it for a browser to break; and the
  server overview, 223 pixels over, where `truncate` — which sets `white-space: nowrap` —
  made a database path's smallest possible width its whole width and pushed the card, the
  grid track and then the page. Those two paths now wrap and can be read, which the third
  path in the same card always could. **And one that is worth knowing about**: a
  screen-reader-only "Actions" label, one pixel wide and invisible to everyone, made every
  listing page scroll 144 pixels. It is `position: absolute`, and an absolutely positioned
  box is confined by its containing block rather than by an ancestor's overflow — so it
  stepped straight out of the table's scroll box and took the document with it. Every box in
  Postulo that is allowed to scroll sideways now establishes a containing block, which is
  what the new `.scroll-x` is for. Checked by a third browser test that asks each page to
  scroll and fails if it moves. (#113)

- **Sixty-two buttons were two pixels too small to hit, and the suite said the pages were
  fine.** The column chooser's *move up* and *move down* buttons were 22 by 22 — a 14-pixel
  chevron with 4 pixels of padding — against the 24 that WCAG 2.2 asks for at AA, repeated
  across every table in the application. They went unnoticed because **axe-core does not
  enforce Target Size (Minimum)**: it reports the rule as needing review rather than as a
  violation, so the accessibility suite returned a clean result on every page carrying them.
  Reading the markup would not have found them either; `p-1` around a `size-3.5` icon is a
  sum nobody does while writing a template. So the fix comes with **the measurement, as a
  test**: every clickable thing on every page the browser suite already visits is asked for
  its box in a real browser and held to 24 by 24, allowing the criterion's own exceptions —
  clear space around a small target, a checkbox measured by the label that switches it, a
  link inside a sentence whose height belongs to the prose around it. Three more failures
  fell out of running it: the dashboard's shortcut links, 20 pixels high in a column with 8
  between them, which is too small *and* too close; the column chooser's own labels at 20;
  and every sortable table header, 12-pixel type on a 16-pixel line with a filter control
  directly beneath. All now carry a `tap-target` class that sets a minimum box without moving
  anything — the criterion measures the box, and padding is only one way to reach it. One
  correction to the report: the checkboxes on *Settings → Plugins* were listed as a probable
  false positive, and they were passing, but on the spacing exception rather than on their
  size — those rows are tall and nothing sits near them. That is a thin thing to rest on, so
  they now pass on size too. (#115)

- **A plugin's own description, licence, author and source link were read and then thrown
  away.** All four came out of every wheel, the confirmation screen showed them once, and the
  record kept none — so an administrator could see who wrote a plugin on the day they
  installed it and never again. They are kept now, and shown on *Server settings → Plugins*.
  **Two of the four were being read from headers modern packaging does not write**: `Home-page`
  is setuptools' old `url=`, and anything using `[project.urls]` emits `Project-URL` instead.
  That was not theoretical — Postulo's own reference plugin is built with hatchling and
  declares its homepage that way, so the project showed no source link for the plugin it
  publishes as the example to copy. `Author` was tried before `Author-email`, which meant
  preferring a bare name over the `First Last <address>` the other field carries. An instance
  that already has plugins fills in the blanks from the `.dist-info` still on its volume
  rather than showing them empty for ever. A plugin may now also declare a `label` and a
  `description` of its own — **optional, and read through helpers rather than added to the
  protocols**, because `runtime_checkable` checks data members and requiring them would have
  silently unloaded every plugin written before they existed. Twenty tests, one of them
  standing guard over exactly that. (#97)

- **The container's health check could not fail.** With `POSTULO_SSL_REDIRECT` on — the
  production default — `SecurityMiddleware` answered `/healthz` with a 301 to
  `https://127.0.0.1:8000/healthz`, before any view ran and before anything touched the
  database. The probe is `curl -fsS`, and `curl -f` fails only on 4xx and 5xx, so it took
  that redirect as success and exited 0. **Every deployment of the shipped image had a
  liveness probe that reported healthy whatever was wrong** — database gone, migrations
  unapplied, every view raising. The 503 the health view returns was unreachable in
  production, and so was the restart that a failing check plus `restart: unless-stopped`
  would have produced. It survived a release for the obvious reason: a check that always
  passes looks exactly like a healthy service. `/healthz` and `/metrics` are exempt from
  the redirect now, both anchored at each end — `SecurityMiddleware` matches with
  `re.search`, so a loose pattern would have exempted every path containing the word, which
  is a worse bug than the one being fixed. **`/logs` is deliberately not exempt**: its
  entries name connections, companies and applications, and a scrape that visibly breaks
  beats personal data crossing a network in clear. Six tests come with it, and each of them
  fails without the fix. (#82)

- **Server settings → People scrolled sideways at every width, including on a desktop.**
  The table needed 967 pixels and the card it sits in gives about 730 whatever the window
  does, so a wider monitor never helped — measured at 1440, 1280, 1024 and 768, and it
  overflowed by roughly 200 pixels at every one. **A third of the table was four buttons**:
  *Change username*, *Make administrator*, *Deactivate* and *Delete account*, spelled out end
  to end, making that column 335 pixels — wider than the email column, and holding no
  information at all. They are a menu now, the same disclosure the account menu in the header
  uses, which takes the table to 676 and leaves room to spare. Every action is still there
  and still a word. **On a phone the rows stop being rows**: a new `table-cards` component
  gives each person a card with its values stacked and labelled. That technique works by
  turning table elements into blocks, which takes the table semantics with it — so every
  element now states its ARIA role, or a screen reader on a narrow screen would hear
  "Administrator" as a loose word rather than as the Role of a row. Eight other tables are
  wrapped the same way and may well overflow too; none of them was measured or touched here.
  (#91)

- **Flags were emoji, and Windows draws those as two letters.** A flag emoji is not a
  character: it is two *regional indicator* code points, and a font is invited — never
  required — to draw the pair as a flag. Segoe UI Emoji never has and Microsoft has said it
  will not, so every Windows machine showed `PT` where everyone else saw a flag. Both places
  this was used carried a comment predicting exactly that and calling it "a legible fallback
  and not a broken image". It is not a fallback; it looks broken, and it looked broken to
  the maintainer on his own desktop. Flags are now SVG images from
  [flag-icons](https://github.com/lipis/flag-icons) (MIT), copied into the repository like
  the icons already were — nothing is fetched from anybody else's server, and the policy
  still says `img-src 'self'`. **The telephone field changed shape**: an `<option>` can hold
  text and nothing else in any browser, so no image could ever have gone in that list. The
  flag moved out beside the closed chooser, where it is arguably more use — visible without
  opening anything — and the list now reads `+351 Portugal`. The chooser is still a native
  `<select>`, because replacing it with something that could hold pictures would trade a
  control that works on every phone and with every screen reader for one that has to be
  re-proved against all of them. With JavaScript off the flag still shows the country the
  page loaded with. On the way, `languages.FLAGS` held the emoji and `phones.FROM_LANGUAGE`
  held the ISO code for the same 24 languages — one fact written down twice, in the codebase
  whose own telephone field refuses to store a country column for precisely that reason. It
  is one map now, and a test fails if the two ever disagree. (#88)

- **The authenticator QR code could not be seen, let alone scanned, in the dark theme.**
  `qrcode` draws the modules as one path filled `#000000` and gives the image no background
  at all — not even a white quiet zone; black is the only colour in the file. On a light
  page that reads perfectly, which is why it shipped. On a dark one it is black on
  near-black: not low contrast, invisible. It is inverted in the dark theme now, which
  flips the modules to white and leaves the transparency alone, so the quiet zone becomes
  the page's own dark — an unbroken margin of one colour, which is what a scanner wants.
  The class hangs off the `qr` tag allauth already puts on that image, so no other image is
  touched. Setting up two-factor authentication is a page nobody visits twice, which is
  exactly how a screen goes years without being looked at in both themes. (#87)

- **Five icons were drawing without their geometry.** The icon tag strips Lucide's fixed
  24-pixel size so that one file can serve a 16-pixel glyph and a 48-pixel illustration —
  but it did so across the whole file rather than the root element, and on a `<rect>` the
  width and height are not a size, they are the shape. The envelope on *Email* lost its box
  and became a lone flap: a "V". *Language and time* lost the month and kept two rings,
  *Dashboard* lost every panel and drew nothing at all, *Overview* lost the screen and kept
  the stand, and the briefcase in the navigation lost the case and kept the handle. Three of
  the five sit side by side in the settings sidebar, which is how they were noticed
  together. The root is stripped now and nothing else. **Nothing could have caught it**: the
  icon tests all asked about the root element, so an icon that rendered as a valid,
  well-labelled, correctly sized, empty box passed every one of them — and the icons are
  `aria-hidden` by design, so axe had nothing to look at either. Three tests come with the
  fix, one of them the reported symptom stated as itself. (#85)

## [0.2.1] — 2026-09-07

### 🐛 Fixed

- **The release run never finished.** `release.yml`'s image job says `runs-on: docker` and
  is guarded by `if: vars.BUILD_IMAGE == 'true'` — but Forgejo queues a job before it
  evaluates the condition, so with no runner advertising that label the job waited for ever
  and the run never completed. v0.2.0 was published, attached and correct, and its run still
  looked unfinished hours later. The comment at the top of `ci.yml` had warned about this
  exact behaviour, about a different file. The destination follows the switch now: with
  image building off the job lands on a runner that exists and is skipped at once, and the
  run completes. (#81)

- **Server settings → Overview returned 500 as soon as a backup existed.** The view worked
  out the newest backup's age as a *span* and the template handed that span to `timesince`,
  which wants a *moment* and reads `.year` off it straight away. The line below it asks
  `age.days >= 7`, which is the right question about a span — both readings sat in the same
  six lines of template and only one matched what the view returned. It returns `made_at`
  and `age` now, and each is used for the thing it answers. **Nothing caught it because no
  test had ever rendered that page with a backup on disk**: with an empty directory the
  template takes the "none yet" branch and never touches the filter, so the unit tests, the
  page-coverage check and the accessibility suite were all looking at the empty state. It
  had been broken for as long as the feature existed, and reachable by anybody who had run
  `manage.py backup` once. Three tests come with the fix — a backup present, one older than
  a week, and an empty directory — so the branch that used to be the only one tested stays
  tested. (#83)

## [0.2.0] — 2026-09-07

### 🔧 Changed

- **The changelog says what kind of change each section holds, at a glance.** The entries
  here are long on purpose — each says *why* rather than what, because the diff already
  says what — and the cost of that is a file whose headings all looked alike. Every
  section now carries a mark: ✨ Added, 🔧 Changed, 🐛 Fixed, 🔒 Security, ⚠️ Deprecated,
  🗑️ Removed. **The word stays beside the mark**, because the word is what reads in a
  terminal, to a screen reader, and in a font that has no glyph for it; the mark is only
  what makes the kind findable while scanning. Applied to every section in the file rather
  than the new ones, since a convention half-applied reads as a mistake. `CONTRIBUTING.md`
  says which to use and `tests/test_changelog.py` refuses a heading that is not one of the
  six or carries the wrong mark — the same way the project already refuses a template that
  names a side of the page. The marks sit inside the sections the release tooling slices
  out, so they travel into a release's notes, which is checked too. (#80)

- **The one thing that pays for this is visible now.** Voluntary support was a sentence
  buried in the README and absent from the wiki entirely. It is the project's *only*
  income — not its main one — so it now carries Buy Me a Coffee's own button: once in the
  README's support section, once on the wiki's opening page, and a link in the sidebar
  every wiki page shows, with a `FUNDING.yml` so the GitHub mirror offers its Sponsor
  button too. The button is **served from this repository rather than hotlinked** — the
  approved asset, a copy of it — so that looking at the page makes no third-party request
  of anybody, which is the rule the application itself follows. What has not changed, and
  is restated beside the button each time, is that **nothing inside Postulo will ever ask
  for money**: no banner in the interface, no prompt, no reminder on the dashboard. Asking
  stays where somebody has to come looking for it. It also says what to give instead,
  since for a great many people looking for work money is the wrong thing to be asked for:
  a bug report, a translation reviewed by somebody who actually speaks the language, or
  telling one person who needs this that it exists. (#79)

### 🐛 Fixed

- **The roadmap contradicted the issue tracker, and the security policy asked for an email
  it never gave.** *Roadmap* said "nothing is planned before somebody has run this for a
  while" while seven issues stood open across four milestones, and listed as *after version
  1* four things that already exist — the browser extension is two extensions, email
  ingestion is `postulo-imap`, calendar synchronisation is `postulo-dav`, and French and
  Portuguese are two of the twenty-four languages that shipped. A page whose stated purpose
  is that "nothing on this wiki reads as a promise" was doing the opposite. It now
  separates what is released from what has landed since, names the milestones in progress,
  points at the tracker as the authority, and lists the eight separate repositories that
  were once future work. `SECURITY.md` said "email the maintainer" and gave no address, on
  the one page whose entire purpose is to be reachable; it gives one now. `docs/PLAN.md`
  stopped at v0.1.0 and still assumed the translations were waiting on contributors, which
  the twenty-four completed catalogues had settled. And the status lines said **0.1.0**
  without saying whether that meant "current" or "the last thing anybody tagged" — it is
  the second, and they say so. (#78)

- **The workflow audit asked GitHub about a repository Forgejo never fetches from GitHub.**
  The last thing keeping continuous integration red: zizmor takes a GitHub token from
  `GH_TOKEN` or `GITHUB_TOKEN`, Forgejo Actions sets the latter, and zizmor then asked
  github.com about `actions/checkout` while holding a Forgejo token — which github.com
  answered with `401 Unauthorized`, failing the job before any audit had run. The token was
  the visible half; the wrong question was the other. Forgejo resolves a bare action name
  against its own instance, so what GitHub believes `actions/checkout@v4` points at is not
  what runs here, and the online audits would have answered confidently about the wrong
  host. It runs `--offline` now, which is the mode that was always correct for this
  instance. It had passed on a laptop because with no token in the environment zizmor
  defaults to offline — the same shape of fault as #76: green only where it was written.
  (#77)

- **Two tests passed only on the machine they were written on.** With the dependency audit
  corrected (#75), continuous integration still failed, and for a reason worth stating: the
  suite was green on Windows and red on Linux. The production-policy test set
  `POSTULO_SECRET_KEY` and then imported the production settings — but the base settings
  module is already imported by the time any test runs, so the key was resolved before the
  variable existed, and the guard fired. It passed locally only because the base settings
  read a `.env` from the repository root, and that file is **gitignored**: what the test
  asserted depended on whether the person running it happened to have a file that is not in
  the repository. It now imports the production settings in a subprocess with an explicit,
  scrubbed environment, which no import order and no developer's `.env` can influence, and a
  second test asserts the guard itself — an instance with no key refuses to start rather
  than inventing one. Separately, the brand check rebuilt every derived image and compared
  the bytes, which two machines do not agree on: Pillow's platform wheels round LANCZOS
  differently, and the three small icons matched while everything from 64 pixels upward did
  not. It compares recorded digests now — of the source, of each derived file, and of the
  size-and-padding table they were built from — and rebuilds nothing, so it answers whether
  these images were built from this source rather than whether this machine rounds like the
  last one. It still fails on a changed mark, an edited or missing derivative, and a changed
  recipe. (#76)

- **Continuous integration had never passed, and the dependency audit had never run.**
  Every one of the thirty-eight runs since the project moved into its organisation was
  red, for a reason that had nothing to do with the code: `uv sync` installs Postulo as an
  editable package, `pip-audit --strict` fails on anything it cannot look up, and Postulo
  is not on PyPI. **The audit stopped at the project and never reported on the
  dependencies at all** — so the README's promise that a fresh disclosure is noticed
  without anyone having to remember to look was not being kept, and a real one would have
  arrived as one more red run among thirty-eight. The lock file is audited now instead of
  the installed environment: `--no-emit-project` leaves Postulo out by construction, which
  keeps `--strict` doing its job, and what is checked is exactly what will be installed
  rather than an environment that also contains the project. Nothing was actually
  vulnerable; both the Python and the Node dependencies were clean when this was found.
  Separately, **nothing on a release branch was being checked at all** — the trigger named
  `main` and the work had moved to per-release branches — so it now runs on those too.
  (#75)

### 🔧 Changed

- **The dashboard and Insights are one page, built from widgets you arrange.** They
  answered the same question — *how is this going?* — at two distances, and you had to
  remember which page held which number. Neither could be adjusted: the dashboard showed
  everybody the same six counters, and Insights showed a response funnel to somebody with
  three applications. Everything both pages held is now a **widget**: seventeen of them,
  each knowing what it computes and which template draws it, registered from
  ``AppConfig.ready`` the way settings sections and capture sources already are. What you
  see and in what order is stored on your profile, and *Settings → Dashboard* arranges it
  with buttons rather than dragging — arranging is done once and then not again, and a form
  that posts works from a keyboard, with a screen reader and with scripts off. **Nothing
  changes for anybody who never opens it**: the standard arrangement is exactly what the
  dashboard showed before, in the same order, and a widget added in a later release joins
  it by itself. Once you have arranged the page it is yours, and new widgets stay off until
  you ask. What is stored is what you *chose*, which is the opposite way round from the
  navigation and is what makes both of those true; never-arranged and arranged-to-nothing
  are different values, so clearing the page stays cleared instead of handing the defaults
  back. Several widgets read the same expensive pass over the event log, and it happens
  once per page however many of them are on it. ``/applications/insights/`` redirects to
  the dashboard, so a bookmark still lands. The browser suite checks a dashboard carrying
  every widget at once, in both themes — markup that a walk of addresses can no longer
  reach. (#44)

### ✨ Added

- **The Europass import reads the current JSON format as well as the legacy XML.** The XML
  is what the old CV editor produced and what an old file on a disk still is; the JSON is
  what europass.europa.eu exports today, so it is the one somebody is most likely to
  arrive with. There is still one file box and one page: a person has *a Europass file* and
  should not have to know which format it is, so the first character decides and the review
  page says which was found. Both readers produce the same intermediate record — a test
  reads two fixtures describing the same career and insists the records match, which is
  what keeps the mapping in one place rather than two. Read defensively, as JSON from
  somewhere else has to be: the same 5 MB cap, a nesting cap measured without recursing,
  no key assumed present and no value assumed to have the type it should. **A file that is
  half right imports the half that is right** and lists what it could not read before
  anything is written, and what was read but not written — a job with no start date — is
  named afterwards rather than dropped quietly. An **ORCID** among the websites in either
  format is lifted out and kept as an identifier (#46), checksum first: a wrong one is
  discarded rather than saved, an existing one is left alone, and orcid.org is asked
  nothing. A date whose month is absent still becomes January; a month that is present and
  unreadable now produces no date at all, because inventing one could misdate a job by
  eleven months. The reader's messages are translated, which the first version's were
  not. (#69)

- **Import a career record from a Europass CV.** Anybody who has applied to an EU
  institution, or through a national employment service, already has one, and typing that
  record in a second time is exactly the work Postulo exists to remove. *Your career →
  Import* reads the Europass XML — what the CV editor produced, and what an older export
  on a disk will be — and matches on tag names rather than the namespace, so all the
  namespaces the format has been through read alike. It happens in two steps: the file is
  read, the page says what was found, and nothing reaches the career record until somebody
  has seen the list and pressed the button. What is held between the steps is the parsed
  record, not the file. **An import only ever adds**: nothing existing is changed or
  removed, a blank profile field is filled but one that already says something is not, and
  a skill heading that already exists is used rather than repeated — duplicates are a
  minute to delete and something lost is not. Europass keeps five CEFR levels per language
  and Postulo keeps one, so the lowest is taken and all five are shown before confirming;
  claiming the best of five is what gets found out in an interview. It is XML from
  somewhere else, so there is a 5 MB cap and **any document type declaration is refused
  before parsing** — that is where entity expansion lives, billion-laughs and external
  entities both need one, and a Europass export has no use for it. `manage.py
  import_europass` does the same from a shell, with `--dry-run`. The reader produces an
  intermediate record rather than writing as it goes, so the JSON format that replaced this
  one (#69) becomes a second front door and not a second mapping. (#54)

- **Document stores: local media built in, an interface for keeping copies elsewhere.**
  A store plugin in the `postulo.stores` group receives a copy of every new document —
  rendered CVs and letters, uploaded files — with enough metadata to file it: kind,
  title, company, role, the application's link, dates, language, tags. Local media stays
  the source of truth and is the same contract applied to this instance, so nothing
  about a plugin is a special case. Copies go out through the scheduler, never inside a
  request, with retries and a growing wait; each document shows *archived* with a link,
  *waiting*, *failed* with the reason, or *not accepted*. A store connection carries a
  switch per document kind, *Send to stores now* tries at once, *Send everything* queues
  what existed before the store did, and the references travel in the export. (#13)
- **Choose what the navigation shows.** Clicking the Postulo wordmark already goes to
  the dashboard, so the *Dashboard* link beside it was a second control for one
  destination. *Settings → Appearance* now lists every item in the row and you tick the
  ones you want; everything there is reachable another way, and the row is what runs out
  of room first on a narrow screen. All of them stay on by default, because a first-time
  visitor has no way of knowing the wordmark is a link. When *Dashboard* is off the
  wordmark takes the job over properly: the active style on the dashboard, and an
  accessible name that says where it goes rather than just naming the instance. (#23)
- **A card can be dragged between board columns.** About sixty lines of delegated
  JavaScript, no library, no new endpoint: a drop sets the card's own status menu and
  submits the form that was already there, so the server path, the event log and the
  timeline entry are the ones the menu produced. The card moves at once and the counts
  follow. The menu stays, because dragging fires on neither a touch screen nor a keyboard,
  and the cards say so to a screen reader. (#35)
- **Companies can have a logo**, from an address you paste, from the company's own
  website, or uploaded. Postulo fetches it once, from the server, and keeps a copy: an
  `<img>` pointing at the company's server would tell them which companies you are looking
  at and when, on every page view, and the content security policy that forbids that stays
  exactly as it is. Everything is decoded and re-encoded to a 256-pixel square, so nothing
  else the file carried survives; SVG is refused until there is a sanitiser for it. Logos
  show beside the name in the companies table, the applications table, the board and the
  company page — and nowhere on a CV or a letter. A company without one shows its
  initials. (#21)
- **Letters come in four kinds.** A cover letter is one page about one posting; a
  motivation letter is longer and sectioned, which is the norm for academic posts, EU
  institutions and much of the continent; a speculative letter has no posting behind it;
  a follow-up note comes after an interview. Each starts from its own shape and theme
  rather than an empty box, the letters page filters by kind, and a rendered motivation
  letter is filed as one. Existing letters are cover letters. Translators get a note about
  *lettre de motivation*, which means the other thing. (#28)
- **Links: portfolios, profiles and video CVs.** Work of yours that already lives
  somewhere is an address, not a file, so it is kept as one: a link with a title, a kind
  and a line of description, on your career record. Links go on a CV as their own section
  and can be sent with an application, where the timeline records them. A video CV is a
  link of the video kind — an unlisted upload somewhere — which is what almost everyone
  does; Postulo does not host video. *Check it answers* asks, once and only when you press
  it, whether an address still responds, because a portfolio that 404s on the day the
  recruiter clicks is the worst outcome there is. (#28)
- **Plugins install from the interface, and survive an upgrade.** *Server settings →
  Plugins* takes a package an administrator uploads: Postulo reads what it says about
  itself — name, version, licence, maintainer, entry points, dependencies, checksum — and
  shows that for confirmation before anything is installed. Plugins live on the data
  volume with a record beside them, so an upgrade cannot lose them; the container
  reinstalls what the record lists and the new image lacks, at boot. A wheel that is not
  pure Python, declares no Postulo entry point, or would move one of Postulo's own
  dependencies is refused with the reason. Plugins can be switched off without being
  removed. `manage.py plugins list | install | remove | disable | enable | sync |
  catalogue` is the same code from the command line. (#38)
- **Signed catalogues.** A catalogue is a JSON index and a detached Ed25519 signature, and
  an administrator configures it as a URL *and* a public key: unsigned, it is not used at
  all. Every wheel is checked against the checksum the signed index carries, so a mirror
  or a hijacked host cannot ship code. Nothing is fetched until somebody presses *Check
  for updates*, and no catalogue is configured by default. (#38)
- **postulo-mcp, a way in for an AI agent — and still no AI inside Postulo.** A small
  server that speaks the Model Context Protocol to whatever agent you already run, and
  Postulo's ordinary API on the other side, with a personal access token whose scopes you
  chose. It can read applications and their timelines, companies, contacts, CVs, letters,
  reminders, interviews and the figures; writing notes, statuses, reminders and letter
  drafts needs both `--write` and a `write` token, and every write lands on the timeline
  with the token's name against it. There is no delete tool of any kind. Lives at
  [postulo/postulo-mcp](https://source.tiagoagueda.com/postulo/postulo-mcp). (#19)
- **Suggestions: what a plugin thinks happened, waiting for you to agree.** A plugin
  reading something outside Postulo is guessing, so nothing it infers is written into the
  record. It files a suggestion — what it says, which application it seems to be about,
  what it would move the application to — and *Applications → Suggestions* is where you
  accept or decline it. Accepting writes it through the same services your own typing
  goes through, with the plugin named on the timeline entry; declining writes nothing. A
  source that names what it read is never asked about the same thing twice. (#34)
- **postulo-imap, reading the job-hunt mailbox.** One folder that you chose — never the
  inbox — read over IMAP, matched to an application by thread, contact, sender domain or
  the words in the message, and classified by phrase lists in English, French and
  Portuguese that anyone can read and extend. Acknowledgements, rejections, interview
  invitations with the dates they name, assessments and offers become suggestions.
  Messages are flagged with an invisible keyword once read, or moved, or left alone
  entirely. Lives at
  [postulo/postulo-imap](https://source.tiagoagueda.com/postulo/postulo-imap). (#34)
- **Synchronisation plugins, and postulo-dav as the first.** A plugin in the
  `postulo.syncs` group keeps records here and elsewhere the same, in both directions;
  the core gives it a connection, a `SyncLink` side table for the remote twin of each
  record, an interval per connection that the scheduler honours, a *Sync now* button, and
  a place on the connection for its last report. postulo-dav puts company contacts in a
  CardDAV address book and interviews in a CalDAV calendar — Nextcloud, Radicale, Baïkal,
  SOGo, Fastmail and the rest — in a dedicated *Postulo* collection each, so a personal
  address book is never imported into a job tracker. The later change wins and the losing
  version is kept as a note; a deletion on the phone never deletes here. Lives at
  [postulo/postulo-dav](https://source.tiagoagueda.com/postulo/postulo-dav). (#16)
- **postulo-paperless, the first store plugin.** Connect a Paperless-ngx archive and
  every document Postulo renders or receives is filed there: the company as
  correspondent, the kind as document type, a `postulo` tag, and a custom field that
  leads back to the application. Consumption is asynchronous and retries are safe,
  because Paperless names the document it already holds; nothing is ever archived twice
  or deleted. Lives at [postulo/postulo-paperless](https://source.tiagoagueda.com/postulo/postulo-paperless). (#15)
- **postulo-apprise, the first plugin built outside the core.** One package, one
  connection, and Postulo can notify through [Apprise](https://github.com/caronc/apprise):
  Telegram, ntfy, Discord, Matrix, Gotify, Pushover, Signal, email and well over a hundred
  other services, each named by one URL. The URLs carry their credentials, so the whole
  field is a secret — encrypted at rest, never shown back — and the connections list
  shows each service with the secret parts masked. A malformed URL is refused on the
  form, not discovered when a reminder falls due, and the instance's destination policy
  applies to every self-hosted service an Apprise URL names. The built-in Email notifier
  stays as it is: the core works with no plugin installed. (#5)
- **Plugins can check a connection and describe it.** A connected plugin may provide
  `validate(config)`, run when the form is submitted with configuration and secrets
  together, and `summary(config)`, one masked line for the connections list. A secret
  may now be a text area, for plugins whose secret is a list. (#5)
- **Plugins in the image.** The Dockerfile takes `POSTULO_EXTRA_PACKAGES`, any number
  of packages in the form pip accepts, and installs them at build time under the core's
  own lock as a constraint, so a plugin cannot change what Postulo pins. A two-line
  `FROM` Dockerfile does the same for anyone who prefers it; both are on *Installing
  Postulo*. (#5)

### ✨ Added

- **Prometheus metrics at `/metrics`, off unless you turn them on.** What exists, what is
  waiting, what has failed, and whether the database answers and the migrations are
  applied — enough to graph a Postulo instance and to be told when a store starts refusing
  everything. Off by default, and while off that address is a 404 rather than a 403, so
  nothing confirms an endpoint is there. `POSTULO_METRICS_TOKEN` gates it when you want it
  gated. **Nothing in it names anybody**: counts only, with no label carrying a person, a
  company, an application or a URL, and a test asserting exactly that. There are
  deliberately no request-rate or latency metrics — three workers means a counter in one of
  them sees a third of the traffic, and your reverse proxy already has those numbers and
  sees all of it. (#50)
- **You can record an ORCID, and other identifiers that say which researcher you are.**
  A name is not an identity: two researchers share one, one researcher publishes under
  three, and a marriage or a transliteration turns one into another. In the places Postulo
  is aimed at first — academic posts, research institutes, EU bodies — the identifier
  somebody actually has is an ORCID, and it is what an application form asks for by name.
  *Your details* now takes one, along with a ResearcherID, a Scopus Author ID, an ISNI or
  anything else under *Other*. Paste the whole address if that is what you have. An ORCID
  is checked against its own checksum, so a typo is caught without Postulo ever asking
  orcid.org anything. They appear in a CV's contact block beside the website, and travel in
  the export. (#46)
- **Every page is now either checked for accessibility or excused in writing.** The browser
  suite ran axe-core over a list of addresses somebody maintained by hand, which meant a
  page added later was simply not on it and nothing said so. About thirty were missing,
  among them the whole suggestions queue, every cover letter page, the contact and industry
  forms, and both error pages. A test now walks the URL resolver and fails unless every
  pattern is either visited by the browser suite or named with a reason — "a file
  download", "a POST from a page that is visited". Adding a page without deciding about it
  is a failing test rather than an omission nobody notices, and it runs in milliseconds
  without a browser, so it fails on a laptop long before CI reaches the slow part. (#66)
- **Phone fields ask which country the number is for.** A recruiter's number written down
  as `06 12 34 56 78` cannot be dialled from anywhere else, and the person writing it down
  is not thinking about that at the time. The field now offers a country beside the box,
  starting at the one your own language suggests, and stores the result in the
  international form so it still works six months and one border later. A number that
  already starts with `+` is taken exactly as it is. A number nobody can parse is kept
  exactly as it was typed, because refusing to save it would be the worst outcome
  available. Numbers are shown grouped so they can be read aloud, and linked so a phone can
  ring them. Postulo does **not** decide whether a number is real: that needs every
  country's numbering plan, and Postulo is not going to dial anything. (#53)
- **The language picker shows each language's flag, and reads as a list.** Twenty-four
  languages is a long list, and the eye finds a flag faster than it reads a name written in
  a script it does not use. The flags are regional indicator characters rather than images:
  two code points, no request, and nothing for the content security policy to block. On
  Windows they render as the two letters instead, which is a legible fallback rather than a
  broken image. The picker is now a list of rows rather than a dropdown, because a dropdown
  could not carry both — an `<option>` takes `lang` and nothing inside it, so the flag would
  have been read out along with the name it decorates. Each language is written in itself,
  marked as being in itself, and the flag is hidden from screen readers. Every flag is
  chosen deliberately rather than derived from the code, since a language is not a country:
  Greek is Greece, Irish is Ireland, and neither code says so. (#52)
- **Postulo has a mark.** A layered paper-cut *P*, in the browser tab, beside the instance's
  name in the header, on a phone's home screen, and at the top of the README and the wiki.
  It is still a work in progress. It lives once at `assets/brand/postulo.png` and everything
  served is derived from it by `scripts/brand.py` and committed, the same way the compiled
  stylesheet is, so an instance runs without needing the tooling and CI fails if the two
  disagree. A web app manifest comes with it, carrying the instance's own name rather than
  always saying Postulo, so an instance installed to a home screen is named the way its
  operator named it.
- **The log can be served at `/logs` for a collector.** One JSON object per line, oldest
  first, with `since` and `limit` so a scraper asks only for what it has not seen — enough
  for Grafana Alloy, Vector or Promtail running elsewhere on your network to read a Postulo
  instance without arranging log shipping off the host. It is **off**, and while off that
  address is a 404 rather than a 403, so nothing confirms the endpoint exists. On, it needs
  a bearer token, and with the endpoint enabled and no token set it refuses to serve and
  records why rather than publishing to whoever finds it. A session is not a substitute: the
  reader is a collector, not a person. Unlike metrics, a log entry names connections,
  companies and applications, so *Hardening* says plainly that this is personal data leaving
  the instance, and the log page itself says whether the endpoint is open. (#51)
- **Read the log from the administration area.** When a notifier will not send or a store
  refuses a document, the answer is in the log, and getting to it meant `docker logs` and a
  shell — on an instance whose administrator is usually the person using it, often from a
  phone. *Server settings → Logs* now shows what the instance has been saying, newest first,
  filtered by level, by which part of Postulo said it, or by a word in the message. Records
  are kept as one JSON object per line in a file under the data volume, rotated by size so
  it cannot fill a disk, and the console still receives everything exactly as before. The
  page says plainly, before showing anything, that these records can name people and their
  applications. `POSTULO_LOG_DIR` set to nothing keeps no file at all. (#49)
- **An operator can say that arriving through the identity provider is enough.** Somebody
  with an authenticator app was asked for a code even when the provider had just checked
  them more carefully than Postulo ever would. *Server settings → Sign-in* now has a switch
  for it, off by default, because Postulo cannot see how a provider authenticated anybody —
  that is a judgement only the person running it can make. It never applies to a password
  sign-in, and it removes nobody's authenticator app. There is deliberately **no** switch
  for passkeys: a passkey is already two factors, so no code is asked for after one, and a
  setting to put that prompt back would only restore the friction that makes people turn
  two-factor authentication off. *Settings → Account* now lists which of your own ways in
  ask for a code and which do not. (#48)
- **Passkeys.** Sign in with a fingerprint, your face or a device PIN, and there is no
  password in it anywhere: nothing to leak in a breach, nothing to reuse elsewhere, and no
  code anybody can talk you into reading out. A passkey is already two factors and the
  browser will only offer it to the address it was made at, so it is proof against a
  convincing copy of the sign-in page as well. Add one under *Settings → Account →
  Passkeys*, then *Sign in with a passkey* on the sign-in page. Signing **up** with one
  stays off: who may register here is the operator's decision and a passkey does not change
  it. The account page says the two things that are easy to find out too late — that a
  passkey is tied to the address you made it at and will not follow the instance to a new
  name, and that browsers refuse the whole mechanism over plain HTTP — and it asks you to
  make recovery codes once a passkey could be your only way in. (#47)
- **Plugins install only built wheels, and say what came with them.** A catalogue's
  signature and the checksum beside it cover a plugin's own file. Its requirements are
  resolved from PyPI when it is installed and are whatever is served that day, which the
  interface did not say anywhere — an administrator reading "signature verified" could
  reasonably conclude that everything installed had been. The plugins page now says where
  that verification stops, both on the page itself and on the confirmation shown before
  anything is fetched. Only built wheels are installed, so a dependency arriving as a
  source package can no longer run its own build script during installation. And every
  package that actually arrived is recorded and listed under the plugin, including the
  ones no wheel's metadata mentions, because a requirement of a requirement never appears
  there. (#61)
- **A way to refuse the identity provider's word about an address.** With single sign-on
  configured, somebody arriving through the provider is signed in as the account holding
  the address it gives — provided the provider marked that address verified, which Postulo
  has always required. That makes the whole arrangement rest on one property of *your*
  provider: that "verified" there means the person proved they hold the address. True of a
  Keycloak or Authentik you run; not automatically true of every endpoint that speaks
  OpenID Connect. `POSTULO_OIDC_LINK_BY_EMAIL=false` requires each person to sign in here
  once and connect the provider from their own account page instead. It stays on by
  default, so nothing changes for an instance already running. *Server settings → Sign-in*
  now says which of the two is in force and what it trusts, and *Hardening* has the
  question to ask about your provider. (#62)

### 🐛 Fixed

- **The error pages were the least accessible pages in Postulo.** The large faint numeral
  on 404, 403 and 500 sat at 1.5:1 against its background, which is unreadable for anybody
  with low vision looking straight at it. It is now legible, and marked decorative so a
  screen reader does not read the status out twice — the heading beneath says what
  happened. The 500 page had no landmark at all, and stated only half its colours, so a
  browser in dark mode gave it a dark link on a light ground. Being the page that renders
  when everything else is broken, it now depends on nothing and states all of them. Nobody
  had looked at these: they were not on the accessibility suite's list. (#66)
- **Every page breached the content security policy.** htmx injects a `<style>` element as
  it starts, carrying its own rules for the request indicator, and production allows no
  inline styles — so the browser refused it and logged a violation on the dashboard, the
  sign-in page and everywhere else. The rules were redundant: Postulo's own stylesheet
  already defines them, precisely because inline styles are not available here. htmx is now
  told not to add them, through a meta tag the policy permits. A browser test serves the
  pages under the production policy and fails on any violation the browser reports, which
  is the part that keeps it true — a policy in a settings file is not evidence that the
  pages obey it. (#68)
- **The progress bars on Insights had stretched, flattened ends.** Each funnel bar was an
  SVG with `preserveAspectRatio="none"`, which scales a hundred-unit-wide drawing across
  the whole column: one horizontal unit became several pixels while one vertical unit
  stayed one, and the corner radius was stretched with everything else, so a semicircle
  rendered as a wide shallow ellipse. It was an SVG because the content security policy
  forbids an inline `style`, so the width had to be an attribute. A `<progress>` element
  is an attribute too, rounds properly at any width because the radius is applied to the
  real box, and is announced by a screen reader with its value rather than needing a label
  that repeats the count. (#45)
- **The language menu did not say which language each of its entries was in.** Every option
  is written in its own language — *български*, *Ελληνικά*, *čeština* — and none of them
  carried a `lang` attribute, so a screen reader pronounced all of them with the rules of
  whatever the interface was set to. Greek read as English is not a word; it is a string of
  letters, or silence. That is WCAG 2.2 success criterion 3.1.2 at level AA, and it fails
  hardest in the one list somebody who cannot read the current language has come to. Each
  option now says what it is. How well translated a language is has moved out of the option
  text and into the group it sits in — *Reviewed by a speaker*, *Machine translation,
  awaiting review*, *Partly translated* — because an English phrase inside an option marked
  as German would be read out in German. The same applies to a CV's and a letter's language
  field. (#64)
- **A cover letter was sent out declaring English, whatever it was written in.** The letter
  theme had `lang="en-GB"` written into it, and there was no field that could have said
  otherwise — the CV had one, the letter did not. A letter written in Portuguese and
  declared English is read aloud with English letter-to-sound rules by whatever the
  recipient uses to read it, which may well be the screen reader of the person deciding on
  the application, and the renderer hyphenates and justifies by the same declaration. A
  letter now carries its own language, and both letters and CVs fall back to the language
  you read Postulo in rather than to English. Both are chosen from the list of the
  instance's languages instead of typed, because a mistyped tag is worse than none. (#65)
- **Every sign-in and account page was rendered without any of Postulo's styling.** The
  four templates allauth's own pages inherit put their card inside a block called
  `content_body`, and allauth's pages fill `content` — so a page replaced the wrapper
  rather than landing inside it, and thirty-one templates had never once been seen with a
  card, a heading size, a styled control or a coloured error. Signing in, signing up,
  resetting or changing a password, managing email addresses, two-factor authentication
  and the third-party connections page all showed full-width unstyled markup: fields
  labelled `Login:` and `Remember Me:`, a submit control that read as a line of text, and
  a wrong-password message in the same black as everything around it. They now sit inside
  the layout, and allauth's building blocks — headings, paragraphs, buttons, form fields,
  tables, alerts — use the same design as the rest of Postulo, so a form there behaves
  like a form anywhere else, error text included. The sign-in field is labelled *Username
  or email* rather than *Login*. The browser accessibility suite reported nothing
  throughout, because everything it can measure was already correct; a test now checks
  that Postulo's own stylesheet actually reaches these pages. (#63)
- **An address was checked and then looked up a second time to connect to.** Capturing a
  posting, checking a portfolio link and fetching a company logo all resolved a hostname,
  refused it if any address it answered with was private, and then handed the name to the
  HTTP client — which looked it up again. A DNS record that lives one second is free to
  answer with a public address for the check and a private one for the connection, which
  is the standard way a check like this is got around. The addresses that passed are now
  the ones connected to. The site is still asked for by name and its certificate still
  checked against that name, so nothing about the request it receives changes. (#58)
- **`X-Forwarded-Proto` was believed from whoever sent it.** Postulo told Django to treat
  any request carrying `X-Forwarded-Proto: https` as secure, which is right behind a
  reverse proxy and wrong the moment an instance is reachable directly — and the Compose
  file publishes a port, so that is a normal way to run one. Anybody who could reach such
  an instance could send the header themselves and skip the redirect to HTTPS. The
  forwarding headers are now believed only from an address a proxy could be at — loopback,
  a Docker network, a LAN, or whatever `POSTULO_TRUSTED_PROXIES` names — and stripped from
  every other request before anything reads them. Nothing changes for the usual
  arrangement, where the proxy is a container beside Postulo or a service on the same host.
  As a consequence, `X-Forwarded-For` from a trusted proxy is now honoured too, so the
  sign-in rate limits count each visitor separately instead of counting the whole instance
  as one. (#60)
- **Sign-in rate limits were counted once per worker and forgotten on every restart.**
  Postulo turns somebody away after ten failed sign-ins a minute from one address, or five
  in five minutes against one account. Those counts live in Django's cache, no cache was
  configured, and Django's default one is a dictionary inside a single process — while the
  container image runs three workers. So the real limit was roughly three times the one
  written down, depending on which worker a request landed on, and every restart or deploy
  wiped it. The default cache is now a table in Postulo's own database: shared by every
  worker, kept across a restart, and created by a migration so there is nothing to run.
  `POSTULO_CACHE_URL` points at Redis or Memcached instead. (#59)
- **Checking a portfolio link could be redirected onto your own network.** *Check it
  answers* validated the address you saved and then let the HTTP client follow redirects
  by itself, so a site answering `302 Location: http://127.0.0.1:9000/` had that request
  made and the status written onto the link. On a self-hosted instance sitting beside a
  router, a NAS and a hypervisor, that turned the button into a scan of that network with
  the answers on display. Every request the check makes is now examined, redirects
  included, and a redirect towards a private or local address is refused and reported as
  such. A redirect between public addresses is followed exactly as before, and this holds
  whatever `POSTULO_CONNECTIONS_ALLOW_PRIVATE` is set to: that setting is about
  connections to services you run, and a portfolio address is public by definition. (#57)
- **The container image would not build.** `collectstatic` with the manifest storage
  the image uses follows references inside JavaScript, and the vendored password-meter
  bundles ended with a pointer to a source map that was never shipped, so the build
  stopped there. The vendoring script now drops that pointer, the build no longer pulls
  the development dependencies back into the image, and both the test suite and the CI
  run `collectstatic` the way the image does.
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

### 🔧 Changed

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

### ✨ Added

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

### ✨ Added

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

### 🔧 Changed

- The default time zone is now `Europe/Paris` rather than UTC.
- WeasyPrint is now the default PDF renderer and is installed with Postulo, rather than
  being one of two optional extras. Chromium remains available as a fallback for machines
  where WeasyPrint's system libraries are impractical.
- A backend now counts as available only if it can actually be imported. WeasyPrint is
  installed but unusable without Pango, and detecting it by presence alone would choose a
  renderer that fails at export time instead of falling back to one that works.
