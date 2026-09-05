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
| **Another account on the same instance** | Their own records, and every page and endpoint with somebody else's id in it | Every user-owned model has an owner; every queryset goes through `for_user()`; every view narrows before it looks up, so another person's record is a `404`, never a `403` that confirms it exists; the test suite sweeps every view and queryset for this. Administrators see accounts, never anyone's applications or documents. |
| **A malicious page that is captured** | The capture parser, the fetcher, anything the HTML reaches | Fetching refuses private and local addresses and re-checks every redirect; the page is parsed into one fixed schema that rejects unknown fields; descriptions are capped; nothing is executed; the result is shown for review, never recorded on its own. |
| **A malicious page the person is on** while signed in | Postulo's forms, through their browser | CSRF tokens on every state-changing request; the API takes a bearer token and ignores the session; every `next` stays on this host; the content security policy allows no inline script and no third-party origin; `frame-ancestors 'none'`; cookies are `Secure` and `HttpOnly`. |
| **A malicious or broken plugin** | Whatever the plugin's code can do in the process | Plugins are installed by the operator, not by people using the instance; a plugin that raises is logged and skipped; connected plugins reach the network only through the guarded client with the operator's destination policy; secrets are encrypted with a key the plugin never sees. This is a trust boundary, not a sandbox: install only plugins you have read. |
| **Someone who obtains a backup or a copy of the database** | Every row, every file | Passwords are hashed; API tokens are stored as hashes, so a copy is not a set of working credentials; connection secrets are encrypted with a key held outside the database (`POSTULO_FIELD_KEY`). Files are not encrypted at rest: encrypt backups (see the wiki's *Hardening* page). |
| **Someone with the token of a browser extension** | The capture API only | Scopes: an extension's token holds `captures` and nothing else, so the worst it can do is fill a review queue the person will decline. Revoke it from Settings. |
| **A vulnerability disclosed in a dependency** | Whatever that library does | Dependencies are audited against the advisory databases on every push and weekly; the process for a disclosure is in `SECURITY.md`. |

## What is out of scope

- The operator's own machine. Root on the host reads everything; that is true of any
  self-hosted software and no code here changes it.
- Denial of service against a personal instance. A reverse proxy's limits are the right
  place for that, and the wiki says how.
- A compromised browser or operating system on the person's side.

## The rules that follow

1. A new model with an owner inherits `OwnedModel`, and a test proves isolation.
2. A new view narrows its queryset with `for_user()` before any lookup.
3. A new file is delivered through `serve_private_file`, never by the web server.
4. A new `next` goes through `postulo.core.redirects.safe_next`.
5. A new outbound request goes through `postulo.plugins.http.client()`.
6. A new secret is stored hashed if it only needs checking, encrypted if it needs using.
7. A new endpoint gets a test in `tests/security/` saying what an attacker would try.
