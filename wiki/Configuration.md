# Configuration

Postulo is configured from the environment. Values are read from a `.env` file in the
project root, or from real environment variables, which take precedence.

`.env.example` in the repository lists the common ones. Nothing here is required in
development; `POSTULO_SECRET_KEY` is required in production and the application refuses
to start without it.

## Core

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_SECRET_KEY` | — | Signs sessions and tokens. **Required in production.** Changing it logs everyone out. |
| `POSTULO_DEBUG` | `false` (`true` in development) | Never enable on a reachable instance: it exposes settings and stack traces. |
| `POSTULO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames this instance answers to. |
| `POSTULO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated origins **including the scheme**, e.g. `https://postulo.example.org`. Needed behind a reverse proxy. |
| `POSTULO_TIME_ZONE` | `Europe/Paris` | The instance default. Each person can override it in their own settings. Also changeable under *Server settings → Defaults* when this variable is not set. |
| `POSTULO_LOG_LEVEL` | `INFO` | Standard Python levels. |
| `POSTULO_ADMIN_URL` | empty | **Where Django's admin lives, and whether it exists at all.** Empty means it is not mounted, which is the default: Postulo's own *Server settings* covers what an operator needs, and an admin nobody mounted is an admin nobody can guess at. Set a path of your own to turn it on — its login is rate-limited when you do. A missing trailing slash is added for you. See *Hardening*. |

## Settings changed from the interface

Policy — whether registration is open, whether `robots.txt` is honoured, the default time
zone, the instance's name and tagline, the language new accounts start with — can be
changed by an administrator under **Server settings** without touching the environment.
Three of those have an environment variable as well, marked in the tables above; **when
the variable is set, it wins**, and the page shows the value read-only and says which
variable pinned it. Leave policy out of `.env` if you would rather change it from the
page. Infrastructure — secrets, the database, hosts, TLS — stays in the environment.

## Which languages this instance offers

Postulo ships a lot of languages, and an instance does not have to offer all of them. Under
**Server settings → Defaults**, *Languages this instance offers* is a list of tick boxes;
untick one and it stops appearing in everybody's language picker.

**Everything is offered until you narrow it**, and leaving it that way is the setting rather
than the absence of one. An instance that offers all of them keeps offering all of them,
including a language a later release of Postulo adds — which a list naming every language
today could not do, because it would have been frozen on the day somebody first saved the
form. Tick them all and it goes back to that state.

**Narrowing never rewrites anybody's choice.** Somebody whose profile says French, on an
instance that stops offering French, reads it in the instance's default language from their
next page onwards — but the stored setting is left exactly where it is, and offering French
again puts them back in French without their having to notice either event. This matters more
than it sounds: an operator narrowing a list of thirty-nine to four would otherwise rewrite
every account that had chosen one of the other thirty-five, and there is no undo for that.

Two saves are refused, and both say why:

- **Offering nothing**, which leaves nobody able to read anything.
- **Withdrawing the language new accounts start in.** The message names that language and
  says to change the default first, rather than only saying no.

An administrator is not exempt from the narrowing: what the instance offers is what the
instance offers. Two rules where one will do is how the two drift apart.

A language that a later release of Postulo *removes* is passed over rather than breaking the
picker — the same way a dashboard widget whose key no longer exists is.

## Database

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_DATABASE_URL` | SQLite at `data/postulo.sqlite3` | A database URL. For PostgreSQL: `postgres://user:password@host:5432/postulo` (install it with `uv sync --extra postgres`). |

SQLite is a perfectly reasonable choice for a personal instance, and makes
[backups](Backups-and-your-data) a single file copy.

## Logs

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_LOG_DIR` | `data/logs` | Where the log file is kept, so *Server settings → Logs* can show it. Empty keeps no file. |
| `POSTULO_LOG_MAX_BYTES` | `5242880` | How large the file grows before it rotates. |
| `POSTULO_LOG_BACKUPS` | `3` | How many rotations are kept. Five megabytes across four files by default. |
| `POSTULO_LOG_LEVEL` | `INFO` | How much is written, to the file and to the console alike. |
| `POSTULO_METRICS_ENABLED` | `false` | Serve Prometheus metrics at `/metrics`. Off, that address is a 404. |
| `POSTULO_METRICS_TOKEN` | empty | A bearer token for `/metrics`. Empty means anybody who can reach the instance can read them. |
| `POSTULO_LOGS_ENDPOINT_ENABLED` | `false` | Serve the log at `/logs` for a collector. Off, that address is a 404. |
| `POSTULO_LOGS_TOKEN` | empty | The bearer token a collector must present. With the endpoint on and this empty, it refuses to serve. |

Records go to the console exactly as before, so `docker logs` is unchanged. The file is the
same records as one JSON object per line, which is what makes the page able to filter them
and what a log collector can read without guessing.

## Behind a proxy

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_TRUSTED_PROXIES` | Loopback, Docker and LAN ranges | Which addresses may set `X-Forwarded-Proto` and `X-Forwarded-For`. Comma-separated CIDRs. Empty trusts nothing. |

The default is where a self-hosted reverse proxy lives, so most instances need not set it.
Everything about why is on [Hardening](Hardening).

## Cache

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_CACHE_URL` | A table in Postulo's own database | Where the cache lives. `redis://localhost:6379/1`, `rediss://…` and `memcache://localhost:11211` all work. |

Postulo keeps the counts behind its rate limits here — how often a password has been got
wrong, how often a reset has been asked for — so the cache has to be one that every worker
shares and that survives a restart. The default does both, and its table is made by a
migration, so there is nothing to set up. Redis or Memcached is faster and behaves the same
way. Pointing this at a per-process cache is the one thing to avoid; see
[Hardening](Hardening).

## Accounts

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_REGISTRATION_OPEN` | `false` | When false, the only way in is an invitation. Also changeable under *Server settings → Sign-in* when this variable is not set. See [Accounts and invitations](Accounts-and-invitations). |

## Passkeys

There is nothing to configure. Passkeys are offered wherever the browser allows them, which
means an instance served over HTTPS, or `localhost` while you are developing. Over plain
HTTP the browser refuses them and the account page says so.

A passkey is registered against the hostname the browser is on and against the instance
name from *Server settings → Defaults*, which is what a password manager shows in its list.
Changing the instance name is safe and only affects passkeys made afterwards; changing the
**hostname** makes every existing passkey unusable at the new one.

## Single sign-on

Optional. Set the first three and a button appears on the sign-in page; leave them unset
and nothing changes. See [Accounts and invitations](Accounts-and-invitations#single-sign-on)
for how accounts are linked and who may be created.

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_OIDC_SERVER_URL` | empty | The provider's issuer URL — where `/.well-known/openid-configuration` lives. For Authentik: `https://auth.example.org/application/o/postulo/`. |
| `POSTULO_OIDC_CLIENT_ID` | empty | The client (application) id registered with the provider. |
| `POSTULO_OIDC_CLIENT_SECRET` | empty | Its secret. |
| `POSTULO_OIDC_NAME` | `Single sign-on` | What the button says. |
| `POSTULO_OIDC_AUTO_SIGNUP` | `false` | Whether the provider may create accounts. Off: existing accounts only. |
| `POSTULO_OIDC_LINK_BY_EMAIL` | `true` | Whether an address the provider says it has verified signs somebody in to the account holding it. Off: each person connects the provider from their own account page. |
| `POSTULO_OIDC_IS_SECOND_FACTOR` | `false` | Whether arriving through the provider counts as the second factor, so no code is asked for as well. Also under *Server settings → Sign-in*. |

Leaving `POSTULO_OIDC_LINK_BY_EMAIL` on means this instance takes the provider's word that
somebody proved they hold an address. That is safe for a provider you run and worth checking
for one you do not; [Hardening](Hardening) has the question to ask.

Register the callback the provider must send people back to, shown under *Server
settings → Sign-in*: `https://your-host/accounts/sso/oidc/login/callback/`. It must match
what the browser reaches exactly, scheme, host and port included. Behind a reverse proxy,
`POSTULO_ALLOWED_HOSTS` and `POSTULO_CSRF_TRUSTED_ORIGINS` need the same host.

## Storage

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_MEDIA_ROOT` | `data/media` | Where uploaded and generated documents are kept. **Never serve this directory from your web server.** |
| `POSTULO_BACKUP_DIR` | `data/backups` | Where `manage.py backup` writes when given no target. `/app/data/backups` in the container. See [Backups and your data](Backups-and-your-data). |
| `POSTULO_STATIC_ROOT` | `staticfiles` | Where `collectstatic` writes. Served by WhiteNoise. |
| `POSTULO_MEDIA_ACCEL_PREFIX` | empty | An nginx `internal` location, e.g. `/protected-media/`. Lets nginx send the bytes after Postulo has authorised the download. |
| `POSTULO_MEDIA_SENDFILE` | `false` | The Apache equivalent, using `mod_xsendfile`. |

Leave both hand-off settings unset and Django streams downloads itself. That is correct
everywhere, and ties up an application worker for the duration of each download — fine
for a personal instance.

## Capture

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_CAPTURE_IGNORE_ROBOTS` | `false` | Postulo honours `robots.txt` when fetching a posting. A person capturing a page they are looking at is not a crawler, but Postulo cannot prove that to the site, so the polite default stands. Turning it off makes you responsible for the requests your instance makes. Also changeable under *Server settings → Capture* when this variable is not set. |

Private and local addresses are refused when capturing, and there is deliberately no
setting to allow them: a self-hosted box that will fetch any address you hand it is a way
to go looking at the rest of your network. See
[Capturing postings](Capturing-postings#what-it-will-not-do).

## Plugins

| Setting | Default | What it does |
| --- | --- | --- |
| `POSTULO_PLUGINS_DIR` | `data/plugins` (`/app/data/plugins` in the image) | Where plugins installed through the interface live. On the data volume, so they survive an upgrade; added to the import path at startup. |
| `POSTULO_PLUGIN_CATALOGUES` | empty | Signed lists of plugins that can be installed by name, as `name\|url\|public-key` entries separated by commas. Without a key there is no catalogue. Fetched only when an administrator asks. **Repositories can also be added from *Server settings → Plugins*; anything named here wins and is shown there greyed out.** |
| `POSTULO_SKIP_PLUGIN_SYNC` | unset | Set to `1` to stop the container reinstalling recorded plugins at boot. |

## Connections

Plugins that talk to another service on a person's behalf — notifiers, document stores,
synchronisation — keep their configuration under *Settings → Connections*.

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_CONNECTIONS_ALLOW_PRIVATE` | `false` | Whether a connection may reach a private or local address. Unlike capture, the destination here is what the person typed, and self-hosted services — a Paperless on the LAN, a mail server in the same Compose network — live on private addresses. Turn it on when yours do. Every request a plugin makes is checked, redirects included. |
| `POSTULO_FIELD_KEY` | empty | The key connection secrets are encrypted under. Unset, a key is derived from `POSTULO_SECRET_KEY` — which means rotating that key makes every stored secret unreadable. Set this once and secrets survive a rotation. Any long random string. |

## Notifications

Postulo sends nothing until a person adds a notification connection under *Settings →
Connections*. The built-in **Email** notifier uses the mail settings below; plugins add
other ways — [postulo-apprise](https://source.tiagoagueda.com/postulo/postulo-apprise)
alone covers Telegram, ntfy, Discord, Matrix, Gotify, Pushover, Signal and over a
hundred more, each named by one URL (see *Plugins in the image* on
[Installing Postulo](Installing-Postulo)). Three things happen without anyone asking: a posting arriving through the
capture API is announced at once; a reminder falling due, and applications going quiet, are
announced by the **scheduler**, which somebody has to run — the same pass also sends the
copies of documents waiting for an external store (see *Keeping copies elsewhere* on
[Files and what you sent](Files-and-what-you-sent)) and runs the synchronisation
connections whose interval has come round:

```sh
# in the container, as a service that loops every five minutes
docker compose -f docker/compose.yml --profile scheduler up -d

# or from the host's cron, every few minutes
*/5 * * * * docker compose -f /data/stacks/postulo/docker/compose.yml exec -T postulo python manage.py send_due_reminders
```

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_PUBLIC_URL` | empty | Where the instance is reached from outside, e.g. `https://postulo.example.org`. Used for the links in messages the scheduler sends, where no request is around to build them from. Unset, those links are bare paths. |

## Documents

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_PDF_BACKEND` | `auto` | `auto`, `weasyprint` or `chromium`. WeasyPrint is the default and ships with Postulo; `auto` prefers it and falls back to Chromium where its system libraries are missing. |

## Email

Email carries the account system's messages — verification links, password resets — and
the built-in **Email** notifier's, for anyone who sets one up under *Settings →
Connections*.

**You do not have to set any of this in the environment.** *Server settings → Email* holds
the same settings, takes effect on the next message without a restart, and has two buttons:
*Test the connection*, which opens a socket, negotiates TLS, signs in and hangs up without
sending anything to anybody, and *Send a test message*, which sends a real one. The
connection test uses what is on the screen rather than what is stored, so a new relay can be
tried before it replaces one that works.

The password entered there is **encrypted at rest**, under the same key as a plugin
connection's secrets — `POSTULO_FIELD_KEY` if you set one, otherwise `SECRET_KEY`. It is
never displayed again, not even its length; the page says only whether one is set. If you
rotate `SECRET_KEY` without having set `POSTULO_FIELD_KEY`, the stored password becomes
unreadable and Postulo falls back to the environment rather than refusing to send.

| Variable | Default |
| --- | --- |
| `POSTULO_DEFAULT_FROM_EMAIL` | `postulo@localhost` |
| `POSTULO_EMAIL_HOST` | `localhost` |
| `POSTULO_EMAIL_PORT` | `25` |
| `POSTULO_EMAIL_HOST_USER` | empty |
| `POSTULO_EMAIL_HOST_PASSWORD` | empty |
| `POSTULO_EMAIL_SECURITY` | from `POSTULO_EMAIL_USE_TLS` |
| `POSTULO_EMAIL_USE_TLS` | `true` |
| `POSTULO_EMAIL_TIMEOUT` | `10` |

**Where both speak, the environment wins**, as it does for every other setting on these
pages: an instance configured through a `.env` since 0.1.0 keeps behaving exactly as it did,
and the page shows such a value read-only with the variable that pins it named beside it.

One thing worth knowing before you delete a variable: if a value is also stored in the
interface, removing the variable hands over to the stored one — **mail starts going
somewhere else without anybody editing anything**. The page says so, in as many words,
whenever both exist.

Only STARTTLS is supported, which is a server on port 587 that upgrades the connection after
opening it. Implicit TLS on port 465 is not offered yet, in the environment or on the page.

**If your host blocks outbound SMTP**, which most residential connections and several VPS
providers do, delivery is a plugin: install a package that speaks an HTTP API instead and
pick it under *Mail transport* on the same page. Postulo ships the SMTP one and names no
vendor; `docs/PLUGINS.md` has the contract. Until you install a second one there is nothing
to choose and the chooser is not shown.

One refusal worth knowing about before you meet it: **the transport carrying your mail cannot
be switched off, and its package cannot be removed, while mail is the only way anybody could
get back into their account.** Postulo counts the accounts that have nothing else — a passkey
signs somebody in without the password they have forgotten; a two-factor recovery code does
not, because it is a second factor and they still need the password. Set up another transport,
or give everyone a passkey, and the refusal lifts on its own.

In development, email is printed to the console instead of being sent, so the settings are
recorded and not used. The page says that too.

### The secret key, and why it is not only a secret key

`POSTULO_SECRET_KEY` signs sessions and password-reset links — and it also **derives the key
that encrypts every stored connection credential**, unless you set `POSTULO_FIELD_KEY`. A
guessable secret key is therefore a guessable encryption key for the passwords people have
given Postulo for their own Nextcloud, Paperless or Telegram.

So Postulo refuses to start on one that is not a secret: shorter than 50 characters, fewer
than 5 distinct characters, or an obvious placeholder (`changeme`, `secret`, anything
beginning `django-insecure-`). Those are Django's own thresholds for `security.W009`, moved
from a warning nobody reads to a refusal you cannot miss.

```sh
python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

**If an upgrade stops your instance starting**, read this before generating a new key.
Replacing `POSTULO_SECRET_KEY` on an instance that has been running signs everybody out
*and* makes every stored connection credential unreadable, because the key that encrypted
them is gone. The order that keeps them:

1. Set `POSTULO_FIELD_KEY` to your **current** secret key and restart. Nothing changes; the
   credentials are now pinned to a key of their own.
2. Then set `POSTULO_SECRET_KEY` to a new, strong one. Sessions end, credentials survive.

`POSTULO_ALLOW_WEAK_SECRET_KEY=true` starts a short key anyway, for the case where this
catches you at an hour when you cannot plan a rotation. It buys an afternoon; it is not an
answer, and it does not apply to a placeholder.

### STARTTLS or implicit TLS, and why the port decides

There are two ways of putting TLS on an SMTP session and they are not interchangeable.

**STARTTLS** connects in the clear, says hello, and asks the server to upgrade the socket.
That is what ports **587** and 25 expect.

**Implicit TLS**, sometimes written SMTPS, hands over a certificate before a single byte of
SMTP is spoken. That is what port **465** expects, and it is what Gmail, Microsoft 365,
Fastmail, OVH and most shared hosting document first.

*Server settings → Email* has one **Connection security** control with three states rather
than a checkbox each, because the two are alternatives and never layers: Django's SMTP
backend refuses to be given both, and a pair of checkboxes would offer a combination that
cannot be saved.

**Point one at the other's port and nothing happens until the timeout**, because each side is
waiting for the other to speak first. That failure looks exactly like a server being down, so
Postulo names it: a connection test that fails on 465 without implicit TLS, or on 587 with it,
says which of the two is wrong before it repeats the timeout. A port that is neither is left
alone — a relay on a port of its own is ordinary for a self-hosted instance, and a settings
page that argues with what you typed is a settings page nobody trusts.

Leave the port empty and it fills in the one that choice normally uses: 587 for STARTTLS, 465
for implicit TLS, 25 for neither. A port you type is never changed.

`POSTULO_EMAIL_SECURITY` is `none`, `starttls` or `ssl`. The older `POSTULO_EMAIL_USE_TLS` is
still honoured for the two states it can express — `true` means STARTTLS — so a `.env` that
has worked since 0.1.0 goes on meaning what it meant. Where both are set, the newer one wins.

### Whether mail is actually getting through

*Server settings → Email* says when mail last went out, and, when the last few messages
failed, says so with what the transport reported. Nothing is probed to answer that: opening
a connection every time somebody looked at the page would mean reading a page sends traffic.
The answer comes from what the last send recorded — the **Send a test message** button goes
through exactly the same path a real message does, so pressing it is what proves a
configuration.

This matters beyond the display, because mail is normally the only way somebody who has
forgotten their password gets back into their account. Postulo will not let you switch the
mail transport off while that is true (see *Plugins*), and that lock used to rest on whether
a transport was *configured*. A relay whose password had been changed, whose host no longer
resolved, or whose credentials had been revoked still counted — so the refusal claimed to be
protecting accounts it was not protecting.

Now the lock rests on delivery. Three consecutive failures with nothing succeeding in between
and mail stops counting as a way back in. Three rather than one, deliberately: a relay that
refuses a single address has told you about that address, not about itself, and a lock that
opens on a typo is worse than one that stays shut an evening longer. An instance that has
never sent anything counts as working, for the same reason.

When mail stops counting, the Email page says how many people that leaves with no way back
into their accounts. **That is the useful fact, and it is true whether or not the lock is
shut** — those accounts are stranded by the broken relay, not by the setting. The lock opens
because keeping it closed does not unstrand anybody; it only stops you installing something
that would.

## Rate limits

Everything these cover needs an account or a token, so none of it is reachable by a stranger.
They bound what somebody who *has* one can make the server do.

| Variable | Default | What it bounds |
| --- | --- | --- |
| `POSTULO_CAPTURE_RATE` | `30/h` | Captures, per account. The tightest of the three, because capture is the only thing that makes your server issue an outbound request to an address somebody else chose. |
| `POSTULO_API_RATE` | `600/h` | API calls, **per token** rather than per account — so a token handed to something that misbehaves can be revoked without touching your own allowance. |
| `POSTULO_ENDPOINT_RATE` | `120/h` | `/logs` and `/metrics`, per calling address. A shared token guards those, so there is no account to count against. |

Written as `N/s`, `N/m`, `N/h` or `N/d`. **Set one to empty to switch it off.** Anything
unreadable also means no limit, deliberately: a mistyped rate should leave you with a working
instance rather than a locked one.

Raise them if they get in your way — an instance with three people has different needs from
one with three hundred, and a bulk import through the API is a normal thing to want. A
refusal is a **429** with a `Retry-After` header, so a well-behaved client waits rather than
hammers.

Two things worth knowing about how they count. The window is fixed rather than sliding, so
the allowance refills at the boundary and a burst can straddle one. And the count is not
strictly atomic on the database cache, so heavy concurrency undercounts slightly. Both err
towards letting somebody through, which is the right way round for a limit whose job is to
stop a machine being ridden rather than to meter billing.

Sign-in, sign-up and password resets are limited separately, by allauth, and are on by
default; see *Hardening*.

## Reaching somebody on a telephone

Postulo can carry a text message, and **ships nothing that sends one**. The capability is a
plugin; the gateway is somebody else's package. That is a decision, not an unfinished edge.

**Every gateway is somebody else's jurisdiction.** An SMS route means a telephone number, a
message and a timestamp reaching Twilio, Vonage or a national aggregator on every send — from
an application whose whole argument is that a self-hoster's data answers to them. Some
operators will refuse that outright and they are right to; it is not Postulo's to impose by
shipping a default, and everything works without one.

**It is a transport, not a notifier**, and the distinction matters. A notifier's credentials
belong to *you* — your Twilio account, your Apprise endpoint. Somebody locked out of their
account is exactly the person whose own gateway may be unreachable, and "the account holder
configured the channel that proves they are the account holder" is circular. So a channel that
carries a way back in is operated by the **instance**, which is what a transport already is.
Mail and text are two mediums of one kind rather than two kinds, because what differs between
them is the payload and nothing else.

**SMS is deliberately not the first way back into an account.** It needs a third party, costs
money per message, and is defeated by a SIM swap — which is not exotic. An administrator
issuing a recovery link needs no third party at all and answers the same question for a
self-hosted instance with one administrator, which is most of them. A text gateway exists so
that a *number can be confirmed at all*; being a recovery route is something it may earn
afterwards, and only once every account has a confirmed number.

Two limits, because two different mistakes want bounding:

| Variable | Default | What it bounds |
| --- | --- | --- |
| `POSTULO_TEXT_RATE` | `5/h` | one account driving the resend button |
| `POSTULO_TEXT_PER_NUMBER_RATE` | `3/h` | one number being made to buzz all afternoon |

The second is the one that matters. A stranger whose number somebody mistyped into a form
never asked to be involved and has no way to switch anything off.

## Contact details, and what proving one means

Postulo holds three kinds of contact detail, and they are not equally provable. One contract
describes all of them, so the rest of the application can ask the same question of each
instead of knowing which is which.

**Two things are being asked, and they are different.** *Is this the right shape* — does this
parse as an email address, does this number carry a country and a plausible count of digits.
And *did somebody prove they hold it* — did they follow the link, did they type the code back.

**Checking stops at the shape, deliberately.** Between "the right shape" and "somebody
answered" there is a middle depth: is this dialling range actually assigned, does this domain
publish an MX record. Postulo does not do that. It needs the numbering plan of every country
— a multi-megabyte library on a constant update treadmill — and the thing that settles the
question is confirmation, which costs nothing and is more conclusive. Refusing the middle is a
decision, not an oversight.

**A number that fails the check is still saved.** This matters more than it sounds. The
number a recruiter dictated over a bad line is still the only number anybody has, and
refusing to record it would be the worst available outcome. What a failed check does is stop
that number counting as something Postulo could reach you on.

| | Checked | Proved | By what |
| --- | --- | --- | --- |
| Email address | shape | yes, by a link | whatever transport carries the mail |
| Telephone number | shape | not yet | nothing on this instance can reach a number |
| Postal address | shape | **never** | — |

**"Never" is a finished answer, not a missing feature.** A postal address can only be proved
by posting something to it. Some services do that; this one is not going to, and the contract
says so rather than leaving the postal channel looking permanently half-built.

**"Not yet" is a different answer from "never".** A telephone number *is* provable — by a
short code typed back, never by a link, because a link in a text message is a phishing lesson
nobody should be teaching. What is missing is anything on this instance that can send to a
number at all. Until that exists, a number cannot be a way back into an account, and Postulo
can say which of the two reasons applies.

`POSTULO_CONFIRMATION_RATE` (default `5/h`) bounds how often one account may ask for a
confirmation to be sent again — one limit across every kind, because a resend button is a way
to make somebody's phone buzz forty times.

## HTTPS

These apply only under the production settings.

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_SSL_REDIRECT` | `true` | Redirects HTTP to HTTPS. `/healthz` and `/metrics` are exempt: those are reached over plain HTTP from inside the deployment, where there is no TLS to redirect to. `/logs` is not exempt, because its entries name people's connections and applications. |
| `POSTULO_HSTS_SECONDS` | `31536000` | One year. |
| `POSTULO_HSTS_INCLUDE_SUBDOMAINS` | `true` | |
| `POSTULO_HSTS_PRELOAD` | `false` | Off deliberately: preloading is close to irreversible and commits every subdomain to HTTPS. Turn it on only if you understand that. |
| `POSTULO_SECURE_COOKIES` | `true` | Session and CSRF cookies are sent only over HTTPS. Set to `false` **only** for an instance reached solely inside a mesh VPN such as NetBird or Tailscale, where the browser sees plain HTTP but the wire is already encrypted — otherwise nobody can sign in. Turn `POSTULO_SSL_REDIRECT` off with it. |
