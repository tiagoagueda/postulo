# Troubleshooting

## "No PDF backend is usable"

WeasyPrint is installed with Postulo, so this almost always means its system libraries
are missing rather than the package:

```sh
sudo apt install libpango-1.0-0 libpangoft2-1.0-0     # Debian and Ubuntu
```

On Windows those libraries are impractical; use the fallback renderer instead:

```sh
uv sync --extra chromium
uv run playwright install chromium
```

If you have installed one and still see the message, check `POSTULO_PDF_BACKEND` — when
it names a specific backend, only that one is tried and no fallback happens.

Everything except export works without any renderer.

## "The weasyprint PDF backend is configured but not usable"

`POSTULO_PDF_BACKEND=weasyprint` is set explicitly, and Pango is missing. Either install
the libraries above, or set the value to `auto` so that Postulo can fall back.

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

## "Nothing resembling a job posting was found on that page"

The page carries no structured data and no usable title. Postings behind a login, or
built entirely by JavaScript after the page loads, cannot be read from the outside —
Postulo fetches HTML, it does not run a browser.

Use **Record an application** and paste the text in. If it is a site you use often, a
plugin can be written for it: see
[docs/PLUGINS.md](https://source.tiagoagueda.com/tiagoagueda/postulo/src/branch/main/docs/PLUGINS.md).

## "That address is on a private or local network"

Capture refuses anything resolving to a loopback, private or link-local address, and
there is no setting to permit it. Paste the posting text in by hand.

## "This site's robots.txt asks automated clients not to fetch that page"

The site has declined. You can set `POSTULO_CAPTURE_IGNORE_ROBOTS=true`, which makes you
responsible for the requests your instance makes, or copy the text in by hand.

## An API token stopped working

Tokens return `401` when missing, mistyped, revoked, or belonging to a disabled account.
Check the token list under **Your details → Capture tokens**: it shows which are revoked
and when each was last used. A lost token cannot be recovered, only replaced.

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
