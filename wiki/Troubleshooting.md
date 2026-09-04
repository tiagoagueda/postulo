# Troubleshooting

## "No PDF backend is installed"

Export needs a renderer, and none is installed. Everything else works without one.

```sh
uv sync --extra chromium && uv run playwright install chromium   # anywhere
uv sync --extra weasyprint                                       # Linux; needs GTK
```

If you installed one and still see the message, check `POSTULO_PDF_BACKEND` — if it names
a specific backend, only that one is tried.

## There is no sign-up form

The instance is invite-only, which is the default. Either get an invitation from a staff
member, or set `POSTULO_REGISTRATION_OPEN=true`. See [Accounts and
invitations](Accounts-and-invitations).

## "This invitation may only be used with the address it was sent to"

The invitation names an email address and you are signing up with a different one. Use the
address it was issued for, or ask for an unbound invitation.

## The admin returns 404

`POSTULO_ADMIN_URL` has moved it. Check your `.env`; it defaults to `admin/`.

## CSRF verification failed

Almost always a reverse proxy. Set `POSTULO_CSRF_TRUSTED_ORIGINS` to your full origin
**including the scheme**:

```sh
POSTULO_CSRF_TRUSTED_ORIGINS=https://postulo.example.org
```

Make sure the proxy forwards `X-Forwarded-Proto`.

## The pages have no styling

`collectstatic` has not run, or `POSTULO_STATIC_ROOT` points somewhere the application
cannot read:

```sh
DJANGO_SETTINGS_MODULE=postulo.config.settings.prod uv run manage.py collectstatic --noinput
```

## Downloads give "File not found"

The database has a record whose file is missing from `MEDIA_ROOT`. Usually the media
directory was not restored alongside the database — see [Backups and your
data](Backups-and-your-data).

## Times are wrong

Set `POSTULO_TIME_ZONE` for the instance, and check the time zone on your own profile,
which overrides it. Times are stored in UTC and displayed in your zone; the stored data is
not wrong, only its presentation.

## Password reset emails never arrive

Postulo only sends email through the account system, and only if it is configured. See
[Configuration](Configuration#email). To change a password without email:

```sh
uv run manage.py changepassword you@example.org
```

## Checking a deployment

This reports anything unsafe about your production configuration:

```sh
DJANGO_SETTINGS_MODULE=postulo.config.settings.prod uv run manage.py check --deploy
```

## Is it running?

`/healthz` returns JSON and checks the database connection. Useful for uptime monitoring.

## Something else

Open an issue on [the repository](https://source.tiagoagueda.com/tiagoagueda/postulo/issues),
with the version (`git rev-parse --short HEAD`), what you did, and what happened. Please
do not paste your `.env`.
