# Security policy

## Reporting a vulnerability

Postulo stores personal documents — CVs, cover letters, and employment history — so
security reports are taken seriously.

Please report vulnerabilities privately rather than opening a public issue. Open a
confidential issue on the [Forgejo repository](https://source.tiagoagueda.com/tiagoagueda/postulo),
or email the maintainer if you have no account there.

Please include the affected version or commit, what an attacker could achieve, and the
steps to reproduce it. You can expect an acknowledgement within a week.

## What the project commits to

Security is one of Postulo's stated missions, not a section of its README: the
application holds CVs, cover letters and employment histories, and it is built as if that
were the only thing that mattered.

- Every record has an owner and every query is scoped to one; a request for somebody
  else's record is a 404, never a confirmation that it exists.
- Files are delivered only through a view that has checked who is asking, with
  `Cache-Control: no-store`.
- The browser runs under a strict content security policy: no inline script, no
  third-party origins.
- Nothing is fetched from the network unless a person asked for it, and never from
  private addresses unless the operator allowed it.
- The test suite contains security tests — ownership sweeps across every view and
  queryset, policy checks, path-traversal and redirect checks — and they fail the build.
- Dependencies are audited against known vulnerabilities in CI on every run and on a
  weekly schedule, so a fresh disclosure in a library is noticed without anyone having to
  remember to look.
- A vulnerability report is handled before any feature work.

## Supported versions

Postulo is pre-alpha. Until version 1.0, only the `main` branch receives fixes.

## Notes for operators

- Postulo makes no outbound network calls except URL captures you explicitly trigger.
  There is no telemetry.
- Uploaded documents are never served directly by the web server; they are delivered
  through an ownership-checked view.
- Set a strong, unique `POSTULO_SECRET_KEY` and serve the application over HTTPS.
  `manage.py check --deploy` verifies the important settings.
