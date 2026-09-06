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

## The cache, and why it is not optional

Postulo counts failed sign-ins, password resets and a few other things in its cache, and
turns somebody away once a count is too high. The default cache is a table in Postulo's own
database, which matters for one reason: every worker reads and writes the same table, so
"ten failed attempts a minute" is ten across the instance rather than ten per worker, and
the count is still there after a restart.

The table is made by a migration; there is nothing to run.

If you have Redis or Memcached, `POSTULO_CACHE_URL` points at it and everything above still
holds:

```sh
POSTULO_CACHE_URL=redis://localhost:6379/1
```

What you should not do is point it at a per-process cache — `locmemcache://` — or at
`dummycache://`. Both make Postulo *look* like it is enforcing a limit while enforcing it
once per worker, or not at all.

## Accounts

- Leave registration closed unless you mean to run a shared instance; invite people.
- Turn on two-factor authentication for every administrator (*Settings → Account*).
- Keep two administrators: the last one cannot be removed, deactivated or deleted, and
  an instance with one is an instance one lost password away from needing the console.

## Plugins and connections

A plugin runs inside Postulo's process with everything Postulo can do, for every person
on the instance. Install only plugins you have read or trust. Postulo refuses a package
that is not pure Python, that declares no Postulo entry point, or whose dependencies would
move one of its own, and it never updates anything by itself — but none of that is a
judgement about what the plugin does once it is running. A plugin you are unsure about can
be switched off without being removed.

A catalogue must be configured with a public key, and its index must be signed with that
key before a single wheel is fetched; every wheel is then checked against the checksum the
signed index carries. Add a catalogue only from people whose review you would accept.

Keep `POSTULO_CONNECTIONS_ALLOW_PRIVATE` off unless a plugin genuinely needs to reach a
service on your own network — with it on, a public hostname that resolves to a private
address is allowed through. It applies to plugin connections and to company logos, and not
to the two things that are public by definition: capturing a posting from a URL, and
checking that a portfolio link answers. Both refuse a private address however that setting
is left, on the first request and on anything they are redirected to.

## Keeping up

Subscribe to the repository's releases. A vulnerability disclosed in a dependency is
handled as `SECURITY.md` describes: a patch release and a pinned issue. Upgrading is a
`docker compose pull && up -d` (see [Installing Postulo](Installing-Postulo)).
