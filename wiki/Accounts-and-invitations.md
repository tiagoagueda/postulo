# Accounts and invitations

Postulo is multi-user, and closed by default.

## Who can sign up

Registration is governed by `POSTULO_REGISTRATION_OPEN`, which defaults to `false`. With
it off, the signup form is not offered at all: the only way in is an invitation. An
instance holding somebody's employment history has no reason to accept strangers.

Set it to `true` only if you genuinely want anyone who finds the URL to be able to create
an account.

## Invitations

Staff members see **Invitations** in the navigation.

An invitation is:

- **single use** — it is spent the moment somebody signs up with it;
- **self-expiring** — fourteen days by default;
- **optionally bound to one email address** — and that binding is enforced at signup, not
  merely suggested, so an invitation addressed to one person cannot be redeemed by
  whoever else ends up holding the link.

Create one, copy the link from the list, and send it however you like. Pending
invitations can be revoked. Accepted ones cannot be deleted, because they are part of the
record of who was let in.

Only staff can issue invitations. To make someone staff, use the Django admin.

## Separation between accounts

Every record in Postulo belongs to exactly one person, and every query is scoped to its
owner. Companies, postings, applications, tags, CVs, letters and files are all private to
the account that created them. Two people on the same instance can each track the same
employer without ever seeing the other's notes.

Asking for a record belonging to someone else returns **not found** rather than
**forbidden** — confirming that a record exists would itself disclose something.

## The administration interface

Django's admin is available, and by default at `/admin/`. On a public instance, move it:

```sh
POSTULO_ADMIN_URL=some-private-path/
```

This is not a security control on its own; it just keeps the noise down.

## Passwords

Password resets are sent by email, so they need working email settings — see
[Configuration](Configuration#email). Without them, reset a password from the command
line:

```sh
uv run manage.py changepassword you@example.org
```
