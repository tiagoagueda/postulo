# Troubleshooting

## Which version am I running?

The page footer says *Postulo X.Y.Z*; *Server settings → Overview* says it in full beside the
Python and Django versions; and `GET /healthz` returns it as `version`, which is the one to
quote in a bug report and the one monitoring can watch to see an upgrade happen.

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

## "The site refused the request (403)"

Bot protection, not a mistake on your part. The page is visible to your browser and
refused to your server.

Paste the page source instead: on the capture page, open *The site refuses Postulo, or
the posting needs a login*, view the source in your browser, and paste it in. Postulo
fetches nothing when you do. See [Capturing postings](Capturing-postings#when-the-site-refuses).

## "Nothing resembling a job posting was found on that page"

The page carries no structured data and no usable title. Postings behind a login, or
built entirely by JavaScript after the page loads, cannot be read from the outside —
Postulo fetches HTML, it does not run a browser.

Use **Record an application** and paste the text in. If it is a site you use often, a
plugin can be written for it: see
[docs/PLUGINS.md](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/docs/PLUGINS.md).

## "That address is on a private or local network"

Capture refuses anything resolving to a loopback, private or link-local address, and
there is no setting to permit it. Paste the posting text in by hand.

## "This site's robots.txt asks automated clients not to fetch that page"

The site has declined. You can set `POSTULO_CAPTURE_IGNORE_ROBOTS=true`, which makes you
responsible for the requests your instance makes, or copy the text in by hand.

## An API token stopped working

Tokens return `401` when missing, mistyped, revoked, or belonging to a disabled account.
Check the token list under **Settings → API tokens**: it shows which are revoked
and when each was last used. A lost token cannot be recovered, only replaced.

## Times are wrong

Set `POSTULO_TIME_ZONE` for the instance, and check the time zone on your own profile,
which overrides it. Times are stored in UTC and displayed in your zone; the stored data is
not wrong, only its presentation.

## Verification or password reset emails never arrive

Postulo only sends email through the account system, and only if it is configured. See
[Configuration](Configuration#email). Until it is, nobody who signs up can complete the
verification that sign-in requires — except the account made with `createsuperuser`,
whose address is trusted, and people who arrived through an invitation bound to their
address.

To verify an address by hand, open the admin (*Accounts → Email addresses*), find it, and
tick *Verified*. To change a password without email:

```sh
uv run manage.py changepassword alex.morgan     # the username
```

## I cannot get past the code prompt

Two-factor authentication is on for the account and the phone with the app is gone. Use
one of the recovery codes shown when it was set up. Without those, someone with a shell on
the server removes the second factor:

```sh
uv run manage.py mfa_reset alex.morgan
```

Then sign in with the password alone and set it up again.

## Single sign-on sends me back with an error

Nine times out of ten the **redirect URI** the identity provider has on file is not the
one Postulo sent. Compare the callback shown under *Server settings → Sign-in* with the
provider's application settings character for character: scheme, host, port, trailing
slash. A test instance reached on plain HTTP inside a mesh needs the provider to accept a
plain-HTTP redirect for it.

If the provider is reached but Postulo refuses the address as unverified, the provider is
not sending `email_verified: true` in its claims; most can be told to. Until then the
person receives a verification link as anyone else would.

## Checking a deployment

This reports anything unsafe about your production configuration:

```sh
DJANGO_SETTINGS_MODULE=postulo.config.settings.prod uv run manage.py check --deploy
```

## Is it running?

`/healthz` returns JSON and checks the database connection. Useful for uptime monitoring.

## Something else

Open an issue on [the repository](https://source.tiagoagueda.com/postulo/postulo/issues),
with the version (`git rev-parse --short HEAD`), what you did, and what happened. Please
do not paste your `.env`.
