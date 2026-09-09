# Hardening

Postulo is built to be as secure as it knows how to be; this page is what the operator
adds around it. None of it is required to run, all of it is worth an hour.

## The reverse proxy

Terminate TLS in front of Postulo and forward `X-Forwarded-Proto`; Postulo redirects to
HTTPS, sets HSTS for a year, and marks its cookies `Secure`.

`X-Forwarded-Proto` is an ordinary request header, so anything that can reach Postulo
directly can send it and claim a connection it does not have. Postulo therefore believes it
**only from an address a proxy could be at**: loopback, a Docker network, a LAN. A request
arriving straight from the internet has that header — and `X-Forwarded-For`,
`X-Forwarded-Host` and the rest — removed before anything reads it.

That default fits the usual arrangement, where the proxy is a container beside Postulo or a
service on the same host, and there is nothing to set. Two cases need a word:

- **Your proxy is somewhere else**, on its own public host. Name it:
  `POSTULO_TRUSTED_PROXIES=203.0.113.7/32`. Give it the narrowest range that works.
- **There is no proxy at all.** `POSTULO_TRUSTED_PROXIES=` trusts nothing, which is right,
  and you should also turn `POSTULO_SSL_REDIRECT` off and read the note beside
  `POSTULO_SECURE_COOKIES` — an instance on plain HTTP has other decisions to make.

Believing `X-Forwarded-For` from a proxy has a second effect worth knowing about: without
it, every request appears to come from the proxy, so the sign-in rate limits count the
whole instance as one visitor and a persistent stranger could use up everybody's allowance.
With it, they count each person separately, as intended.

Add at the proxy what an application cannot do for itself:

- **Rate limits** on `/accounts/login/`, `/accounts/signup/`, `/accounts/password/` and
  `/api/v1/` — Postulo limits sign-in attempts itself, but a proxy limit is cheaper and
  earlier.
- **A body size limit** a little above the largest upload you expect (uploads are capped
  at 20 MB, avatars at 5 MB, Europass files at 5 MB, spreadsheets at 2 MB).
- **Never serve `data/media` yourself.** Uploaded CVs are delivered only through a view
  that has checked who is asking. If you use `X-Accel-Redirect` or `X-Sendfile`, mark the
  location `internal`.
- The security headers Postulo sends (a strict content security policy, `nosniff`,
  `same-origin` referrer, `DENY` framing) can stay as they are — and **check that your proxy
  passes them through unchanged**, because some replace them. Traefik, on the maintainer's
  own test instance, rewrites `X-Frame-Options: DENY` to `SAMEORIGIN` and pins HSTS to a
  year whatever the application asks for. The framing case is not a hole, since
  `frame-ancestors 'none'` in the policy covers every browser that matters, but a header the
  application sets and nobody receives is worth knowing about, and no test of the source can
  find it. `curl -sSI https://your-instance/` and compare with what the container sends.
  Do not loosen the policy
  to add analytics — there is no place for a third-party script in a page that shows
  somebody's employment history. The pages are served under that policy in a real browser
  by the test suite, which fails if any of them provokes a single violation, so it should
  stay quiet in yours too. If you point a reporting endpoint at it and see something, it is
  worth telling us about.

## Secrets

- `POSTULO_SECRET_KEY`: long, random, and never reused from another instance. Postulo
  refuses to start on one that is short, repetitive or an obvious placeholder — Django's own
  `security.W009` thresholds, moved from a warning to a refusal, because this key does more
  here than sign sessions.
- `POSTULO_FIELD_KEY`: the key that encrypts connection secrets (tokens for notifiers and
  stores). Keep it **outside** the database backup — a backup with both is a backup with
  the secrets in clear. **Set it even if you never rotate anything**: without it those
  secrets are encrypted under `POSTULO_SECRET_KEY`, which means you cannot ever change your
  signing key without losing them. Setting it now is what makes that possible later, and it
  is checked for strength the same way.
- API tokens are shown once and stored hashed; a lost one is replaced, not recovered.

## Backups

A backup holds everything. Encrypt it at rest (`age`, `gpg`, or an encrypted volume), keep
it somewhere the web server cannot reach, and test a restore once. See
[Backups and your data](Backups-and-your-data).

## Monitoring

`/metrics` serves Prometheus metrics: how many applications, listings and companies exist,
how many document copies are waiting or have failed, whether the database answers and
whether the migrations are applied. **It is off**, and while it is off that address is a
404 rather than a 403, so nothing tells a stranger the endpoint exists.

```sh
POSTULO_METRICS_ENABLED=true
POSTULO_METRICS_TOKEN=        # optional; see below
```

**Nothing in it names anybody.** Counts only: how many applications exist, never whose;
how many copies are waiting, never for what. No label carries a person, a company, an
application or a URL, and a test asserts it — a metric with somebody's identifier in a
label is a record of what they are doing, exported somewhere else, and calling it
monitoring does not change that.

That is why the token is optional here and mandatory for the log endpoint. Left empty,
`/metrics` is readable by whoever can reach the instance, which is a sensible thing on a
private network and a decision you should make deliberately; *Server settings → Logs* says
which of the two is in force.

**There are no request-rate or latency metrics, on purpose.** The image runs three workers,
and a counter living in one of them sees a third of the traffic — a graph quietly showing a
third of the traffic is worse than no graph. Your reverse proxy already has those numbers,
sees every request, and is the right place for them.

## The log endpoint

`/logs` serves the same records as *Server settings → Logs*, one JSON object per line, for a
collector such as Grafana Alloy or Vector running elsewhere on your network. **It is off**,
and while it is off that address is a 404 rather than a 403, so nothing tells a stranger the
endpoint exists.

If you turn it on, set a token:

```sh
POSTULO_LOGS_ENDPOINT_ENABLED=true
POSTULO_LOGS_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
```

With the endpoint on and no token, Postulo refuses to serve anything and records why. That
is deliberate: an unauthenticated log endpoint is a data leak with a URL, and a forgotten
variable should fail loudly rather than publish quietly.

Be clear about what this is. Metrics can be made to carry nothing about anybody. A log entry
cannot: explaining that a delivery failed means naming the connection, and often the company
and the application it was for. Turning this on is personal data leaving the instance over
HTTP, to be treated the way you would treat a backup. If you can already read the
container's stdout, do that instead — it is the same records and nothing new is exposed.

## The cache, and why it is not optional

Postulo counts failed sign-ins, password resets, captures, API calls and scrapes of `/logs`
and `/metrics` in its cache, and turns somebody away once a count is too high. The default cache is a table in Postulo's own
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

**What the counts cover.** allauth's, on sign-in, sign-up and password reset, which have
always been there. And Postulo's own, on the three things an account could otherwise do
without limit: capture, the API, and the two token-guarded endpoints. Capture is the one that
matters, because it is the only thing here that makes *your* server issue an outbound request
to an address somebody else supplied — `POSTULO_CAPTURE_RATE`, and see *Configuration* for
the numbers and how to raise them. Nothing in that group is reachable without an account, so
none of it was ever a stranger's to abuse; it was a ceiling on the people you have already
let in.

## Single sign-on, and what it asks you to trust

With an OpenID Connect provider configured, somebody arriving through it is signed in as
the Postulo account holding the address the provider gives, and the two are connected from
then on. That is the convenience worth having, and it is worth knowing exactly what it
rests on.

**Postulo only ever matches an address the provider marked verified.** An unverified claim
links to nothing at all. So the security of the arrangement comes down to one question
about your provider, not about Postulo:

> Does this provider only call an address verified when the person actually proved they
> hold it?

For a Keycloak, Authentik, Pocket ID, Zitadel or Kanidm you run yourself, the answer is
almost certainly yes, and there is nothing to do. For an endpoint you do not control, check
before you rely on it: a provider that lets somebody type any address into their profile and
calls it verified turns "sign in with single sign-on" into "sign in as anyone whose address
you know".

If you cannot answer it, close the door:

```sh
POSTULO_OIDC_LINK_BY_EMAIL=false
```

Then single sign-on signs in only accounts that have already connected the provider
deliberately, from *Settings → Connections*. A person signs in with a password once and
links it themselves.

Two related things, so the picture is complete:

- `POSTULO_OIDC_AUTO_SIGNUP` is a **different** door, and it is shut by default: it governs
  whether the provider may *create* an account. Linking to one that already exists is what
  the setting above governs.
- If a local account's own address was never verified, signing into it through the provider
  wipes that account's password. That is deliberate and it protects you: somebody who
  registered with your address before you did, and knows the password they chose, is locked
  out at the moment you first sign in. A verified local address is left alone.

## When a second factor is asked for

Postulo asks for a code from an authenticator app whenever the account has one set up. Two
sign-ins are treated differently, and it is worth knowing which and why.

**A passkey never gets the prompt**, and there is no setting for that. It is already two
factors — the device somebody has, released by a fingerprint, a face or a PIN — so asking
for a code afterwards is a second lock on a door that already has one. That friction is
what makes people turn two-factor authentication off altogether, which leaves them worse
off than before.

**Single sign-on gets the prompt unless you say otherwise.** Your identity provider has
just done the checking Postulo is about to repeat, and on a company or university provider
it very often did it with something stronger than six digits. But Postulo cannot see that:
how the provider authenticated somebody is not in what it sends back. So it is your call,
because you are the only one who knows what your provider actually enforces:

```sh
POSTULO_OIDC_IS_SECOND_FACTOR=true
```

or the switch under *Server settings → Sign-in*. Turn it on when you run the provider and
know it requires a second factor of its own. Leave it off otherwise: with it on, anybody
who gets through your provider is through Postulo, and the code that would have caught them
is not asked for.

Two things it never does. It does not apply to a **password** sign-in: somebody with an
authenticator app who signs in with a password is asked for the code, always. And it
removes nobody's authenticator app — it changes when a code is asked for, not whether the
account has one. Each person can see which of their own ways in are complete under
*Settings → Account*.

## Django's admin

**It is off.** `POSTULO_ADMIN_URL` is empty by default and nothing is mounted, so there is no
`/admin/` on a Postulo instance unless you put one there. That is deliberate: *Server
settings* covers people, sign-in policy, plugins, email, logs and defaults, which leaves the
admin as a developer's convenience — and on a self-hosted box, mostly a second and less
careful way into the same data, at the first path anybody would try.

It was not always off. Before 0.3.0 it defaulted to `admin/`, which published Django's own
username-and-password form on every instance whose operator had not read one line of the
settings file.

If you want it, set a path of your own:

```dotenv
POSTULO_ADMIN_URL=back-office-7f3a/
```

Two things to know when you do. The login is **rate-limited** to the same `10/m/ip,
5/300s/key` as a failed sign-in here, through the same limiter and the same cache — Django's
admin has a login view of its own, and allauth's limits do not reach it. And a path is not a
secret: treat it as one fewer thing being guessed at, not as protection. Put the admin behind
your VPN or an authentication layer at the proxy if you keep it.

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

That signature covers the plugin's own file and stops there. A plugin's **requirements**
are fetched from PyPI when it is installed, and are whatever is served that day, together
with whatever they need in turn — so trusting a plugin means trusting its dependency list,
and the confirmation page shows you that list before anything is fetched. Postulo installs
only built wheels, so nothing runs a build script on the way in, and records every package
that arrived: open a plugin's entry under *Server settings → Plugins* to see what is
actually in your instance.

Keep `POSTULO_CONNECTIONS_ALLOW_PRIVATE` off unless a plugin genuinely needs to reach a
service on your own network — with it on, a public hostname that resolves to a private
address is allowed through. It applies to plugin connections and to company logos, and not
to the two things that are public by definition: capturing a posting from a URL, and
checking that a portfolio link answers. Both refuse a private address however that setting
is left, on the first request and on anything they are redirected to.

Where the check does apply, Postulo connects to an address it has already approved rather
than looking the name up again, so a record that answers differently a second later cannot
send the request somewhere the check never saw. The site is still asked for by name, and
its certificate is still checked against that name.

## Keeping up

Subscribe to the repository's releases. A vulnerability disclosed in a dependency is
handled as `SECURITY.md` describes: a patch release and a pinned issue. Upgrading is a
`docker compose pull && up -d` (see [Installing Postulo](Installing-Postulo)).

## The image and Debian's updates

Postulo's image is built on `python:3.14-slim-bookworm` and runs `apt-get upgrade` at build
time, so a fresh build takes whatever security updates Debian has published. That is worth
knowing for two reasons.

**Rebuilding is how you get patched.** A running container never updates itself; nothing in
it reaches out for packages, by design. When Debian publishes a security update for something
in the image — including packages Postulo never asked for, which arrive with Python and with
Pango — the way to get it is to pull a newly built image, or to rebuild without a cache.
Pulling the same tag you already have does nothing if the tag has not been rebuilt since.

**The base image is not pinned to a digest, deliberately.** Pinning would make two builds of
the same commit produce the same image, which is a real thing to want. It would also mean
Debian's updates arrive only when somebody remembers to bump the digest — and a manual step
nobody performs is exactly how a fixable High-severity finding sat in the image unnoticed.
Between an image that drifts towards being patched and one that reliably stays unpatched,
Postulo takes the first. Each release is published by digest, so the artefact you actually
run is still identified exactly.

**Most findings in a Debian base image have no fix, and that is normal.** Scanning the image
will report dozens of HIGH and several CRITICAL entries that Debian has triaged as not
warranting an update. They are not ignorable by choice; there is nothing to install. Scan
with `--ignore-unfixed` (Trivy) or `--only-fixed` (Grype) to see the findings you can
actually act on, which is usually none or one.

## Where the server is allowed to dial

Postulo makes outbound connections on your behalf, and every one of them goes somewhere
*somebody typed*. Two rules cover all of it.

**A capture URL is refused if it is private or local, always.** That address came off a
stranger's page, and a self-hosted instance sits on a network with a router, a NAS and
whatever else at addresses a stranger would love the server to fetch for them.

**Everything else — a plugin connection, a mail server — is refused if it is private,
unless you have said otherwise.** A Paperless on your own LAN is the entire point of a
connection, and a mail relay in the same Compose network is the same case, so one switch
governs both:

```
POSTULO_CONNECTIONS_ALLOW_PRIVATE=true
```

One decision, made once, by the person who owns the machine. The refusal names this variable
when it happens, so nobody has to go looking for it.

**`POSTULO_EMAIL_HOST` is exempt.** A host set in the environment is a line in a file only
you can edit — and the default it carries is `localhost` — so checking it would refuse the
default configuration of every instance that has never opened the Email page. A host stored
from *Server settings → Email* is checked.

Three things happen on every one of these connections, and the third is the one that is
usually missing:

1. **Private and loopback addresses are refused** under the policy above.
2. **Every address the name answers with is checked**, not just the first. A name that
   resolves to one public address and one private one would otherwise be a way straight
   through.
3. **The connection goes to the address that was checked.** A name with a one-second
   lifetime can answer publicly for the check and privately a moment later; resolving twice
   leaves exactly that window. The certificate is still proved against the name you typed,
   not against the number.

**The refusal happens before anything is dialled.** Every private address gets the same
answer whether something is listening on it or not, because nothing ever finds out — which
is what stops the *Test* button being a port scanner with a form around it.
