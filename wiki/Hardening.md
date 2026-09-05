# Hardening

Postulo is built to be as secure as it knows how to be; this page is what the operator
adds around it. None of it is required to run, all of it is worth an hour.

## The reverse proxy

Terminate TLS in front of Postulo and forward `X-Forwarded-Proto`; Postulo redirects to
HTTPS, sets HSTS for a year, and marks its cookies `Secure`. Add at the proxy what an
application cannot do for itself:

- **Rate limits** on `/accounts/login/`, `/accounts/signup/`, `/accounts/password/` and
  `/api/v1/` — Postulo limits sign-in attempts itself, but a proxy limit is cheaper and
  earlier.
- **A body size limit** a little above the largest upload you expect (uploads are capped
  at 20 MB, avatars at 5 MB, spreadsheets at 2 MB).
- **Never serve `data/media` yourself.** Uploaded CVs are delivered only through a view
  that has checked who is asking. If you use `X-Accel-Redirect` or `X-Sendfile`, mark the
  location `internal`.
- The security headers Postulo sends (a strict content security policy, `nosniff`,
  `same-origin` referrer, `DENY` framing) can stay as they are; do not loosen the policy
  to add analytics — there is no place for a third-party script in a page that shows
  somebody's employment history.

## Secrets

- `POSTULO_SECRET_KEY`: long, random, and never reused from another instance.
- `POSTULO_FIELD_KEY`: the key that encrypts connection secrets (tokens for notifiers and
  stores). Keep it **outside** the database backup — a backup with both is a backup with
  the secrets in clear.
- API tokens are shown once and stored hashed; a lost one is replaced, not recovered.

## Backups

A backup holds everything. Encrypt it at rest (`age`, `gpg`, or an encrypted volume), keep
it somewhere the web server cannot reach, and test a restore once. See
[Backups and your data](Backups-and-your-data).

## Accounts

- Leave registration closed unless you mean to run a shared instance; invite people.
- Turn on two-factor authentication for every administrator (*Settings → Account*).
- Keep two administrators: the last one cannot be removed, deactivated or deleted, and
  an instance with one is an instance one lost password away from needing the console.

## Plugins and connections

A plugin runs inside Postulo's process with everything Postulo can do. Install only
plugins you have read or trust, and keep `POSTULO_CONNECTIONS_ALLOW_PRIVATE` off unless a
plugin genuinely needs to reach a service on your own network — with it on, a public
hostname that resolves to a private address is allowed through.

## Keeping up

Subscribe to the repository's releases. A vulnerability disclosed in a dependency is
handled as `SECURITY.md` describes: a patch release and a pinned issue. Upgrading is a
`docker compose pull && up -d` (see [Installing Postulo](Installing-Postulo)).
