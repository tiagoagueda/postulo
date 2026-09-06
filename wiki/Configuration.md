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
| `POSTULO_ADMIN_URL` | `admin/` | Moves Django's admin off a guessable path. Include the trailing slash. |

## Settings changed from the interface

Policy — whether registration is open, whether `robots.txt` is honoured, the default time
zone, the instance's name and tagline, the language new accounts start with — can be
changed by an administrator under **Server settings** without touching the environment.
Three of those have an environment variable as well, marked in the tables above; **when
the variable is set, it wins**, and the page shows the value read-only and says which
variable pinned it. Leave policy out of `.env` if you would rather change it from the
page. Infrastructure — secrets, the database, hosts, TLS — stays in the environment.

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
| `POSTULO_PLUGIN_CATALOGUES` | empty | Signed lists of plugins that can be installed by name, as `name\|url\|public-key` entries separated by commas. Without a key there is no catalogue. Fetched only when an administrator asks. |
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
Connections*. Prove the settings with *Server settings → Email → Send a test message*.

| Variable | Default |
| --- | --- |
| `POSTULO_DEFAULT_FROM_EMAIL` | `postulo@localhost` |
| `POSTULO_EMAIL_HOST` | `localhost` |
| `POSTULO_EMAIL_PORT` | `25` |
| `POSTULO_EMAIL_HOST_USER` | empty |
| `POSTULO_EMAIL_HOST_PASSWORD` | empty |
| `POSTULO_EMAIL_USE_TLS` | `true` |
| `POSTULO_EMAIL_TIMEOUT` | `10` |

In development, email is printed to the console instead of being sent.

## HTTPS

These apply only under the production settings.

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_SSL_REDIRECT` | `true` | Redirects HTTP to HTTPS. |
| `POSTULO_HSTS_SECONDS` | `31536000` | One year. |
| `POSTULO_HSTS_INCLUDE_SUBDOMAINS` | `true` | |
| `POSTULO_HSTS_PRELOAD` | `false` | Off deliberately: preloading is close to irreversible and commits every subdomain to HTTPS. Turn it on only if you understand that. |
| `POSTULO_SECURE_COOKIES` | `true` | Session and CSRF cookies are sent only over HTTPS. Set to `false` **only** for an instance reached solely inside a mesh VPN such as NetBird or Tailscale, where the browser sees plain HTTP but the wire is already encrypted — otherwise nobody can sign in. Turn `POSTULO_SSL_REDIRECT` off with it. |
