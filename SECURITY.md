# Security policy

## Reporting a vulnerability

Postulo stores personal documents — CVs, cover letters, and employment history — so
security reports are taken seriously.

Please report vulnerabilities privately rather than opening a public issue. Open a
confidential issue on the [Forgejo repository](https://git.tiagoagueda.com/tiagoagueda/postulo),
or email the maintainer if you have no account there.

Please include the affected version or commit, what an attacker could achieve, and the
steps to reproduce it. You can expect an acknowledgement within a week.

## Supported versions

Postulo is pre-alpha. Until version 1.0, only the `main` branch receives fixes.

## Notes for operators

- Postulo makes no outbound network calls except URL captures you explicitly trigger.
  There is no telemetry.
- Uploaded documents are never served directly by the web server; they are delivered
  through an ownership-checked view.
- Set a strong, unique `POSTULO_SECRET_KEY` and serve the application over HTTPS.
  `manage.py check --deploy` verifies the important settings.
