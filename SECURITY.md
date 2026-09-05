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

## When a vulnerability is disclosed in a dependency

The process, so that it is the same every time and needs no thinking on a bad day:

1. **Notice.** The `security` job in CI audits every locked dependency on every push and
   every Monday; the maintainer is also subscribed to the security announcements of the
   direct dependencies (Django, django-allauth, django-ninja, Pillow, WeasyPrint, httpx,
   cryptography). A failing audit is a page, not a chore.
2. **Assess** within a day: does Postulo use the affected code path, and can it be reached
   from outside? Record the answer in a confidential issue.
3. **Fix**: upgrade the dependency (`uv lock --upgrade-package NAME`), run the suite, and if
   Postulo's own code needs a change make it in the same commit.
4. **Release** a patch version (`vX.Y.Z+1`) through the release workflow, the same day for
   anything reachable without signing in, within the week otherwise.
5. **Tell operators**: a `Security` section in that release's changelog entry, and a pinned
   issue on the repository that says what was affected, what to upgrade to, and whether
   anything else — rotating a token, say — is needed.

Postulo itself: a report about Postulo's own code follows the same steps from 2 onwards,
with the reporter kept informed and credited if they wish.

## Supported versions

Until version 1.0, only the latest release and the `main` branch receive fixes. Upgrading
is a `docker compose pull`; there is no reason to stay behind.

## Notes for operators

- Postulo makes no outbound network calls except URL captures you explicitly trigger.
  There is no telemetry.
- Uploaded documents are never served directly by the web server; they are delivered
  through an ownership-checked view.
- Set a strong, unique `POSTULO_SECRET_KEY` and serve the application over HTTPS.
  `manage.py check --deploy` verifies the important settings.
