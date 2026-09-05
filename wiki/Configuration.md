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
| `POSTULO_TIME_ZONE` | `Europe/Paris` | The instance default. Each person can override it in their own profile. |
| `POSTULO_LOG_LEVEL` | `INFO` | Standard Python levels. |
| `POSTULO_ADMIN_URL` | `admin/` | Moves Django's admin off a guessable path. Include the trailing slash. |

## Database

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_DATABASE_URL` | SQLite at `data/postulo.sqlite3` | A database URL. For PostgreSQL: `postgres://user:password@host:5432/postulo` (install it with `uv sync --extra postgres`). |

SQLite is a perfectly reasonable choice for a personal instance, and makes
[backups](Backups-and-your-data) a single file copy.

## Accounts

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_REGISTRATION_OPEN` | `false` | When false, the only way in is an invitation. See [Accounts and invitations](Accounts-and-invitations). |

## Storage

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_MEDIA_ROOT` | `data/media` | Where uploaded and generated documents are kept. **Never serve this directory from your web server.** |
| `POSTULO_STATIC_ROOT` | `staticfiles` | Where `collectstatic` writes. Served by WhiteNoise. |
| `POSTULO_MEDIA_ACCEL_PREFIX` | empty | An nginx `internal` location, e.g. `/protected-media/`. Lets nginx send the bytes after Postulo has authorised the download. |
| `POSTULO_MEDIA_SENDFILE` | `false` | The Apache equivalent, using `mod_xsendfile`. |

Leave both hand-off settings unset and Django streams downloads itself. That is correct
everywhere, and ties up an application worker for the duration of each download — fine
for a personal instance.

## Capture

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_CAPTURE_IGNORE_ROBOTS` | `false` | Postulo honours `robots.txt` when fetching a posting. A person capturing a page they are looking at is not a crawler, but Postulo cannot prove that to the site, so the polite default stands. Turning it off makes you responsible for the requests your instance makes. |

Private and local addresses are refused when capturing, and there is deliberately no
setting to allow them: a self-hosted box that will fetch any address you hand it is a way
to go looking at the rest of your network. See
[Capturing postings](Capturing-postings#what-it-will-not-do).

## Documents

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTULO_PDF_BACKEND` | `auto` | `auto`, `weasyprint` or `chromium`. WeasyPrint is the default and ships with Postulo; `auto` prefers it and falls back to Chromium where its system libraries are missing. |

## Email

Postulo does not send you notifications. Email is used only by the account system, for
password resets and address confirmations, so an instance with a single user who never
forgets their password can ignore this section entirely.

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
