# Accounts and invitations

Postulo is multi-user, and closed by default.

## Who a person is

Every account has three things, all obligatory:

- **A username** — 3 to 32 lowercase letters, digits, dots, underscores or hyphens. It is
  what you sign in with, and what other people on a shared instance see. Chosen at
  signup; changed under *Settings → Account*.
- **A full name** — first and last. Every document starts from it, so Postulo asks for
  it up front rather than printing a CV with a blank at the top.
- **An email address** — unique across the instance, and **verified**: a link is sent to
  it, and the account cannot sign in until the link has been followed. The address works
  for signing in too, interchangeably with the username.

An account may hold up to five addresses (*Settings → Account → Email addresses*), each of
them verified by its own link. One is **primary**: it receives Postulo's mail and is the
one an export records. An address cannot be made primary until it has been verified.

Accounts that existed before verification was required were marked verified when the
instance was upgraded: they had been signing in by those addresses all along.

## Two-factor authentication

Optional, per person, under **Settings → Account → Two-factor authentication**. Once it is
on, signing in asks for a six-digit code from an authenticator app after the password, so
a leaked password alone is not enough.

- **Setting up**: scan the QR code with any authenticator app (Aegis, 2FAS, Ente Auth,
  Google Authenticator, the one built into your password manager), or type the key, then
  confirm with one code. Postulo asks for your password again first.
- **Recovery codes**: ten single-use codes, shown when you set up and available again from
  the same page. Keep them somewhere that is not your phone; each signs you in once when
  the phone is not to hand.
- **Trust this browser**: after a code, Postulo offers to skip the question on that browser
  for thirty days. Decline on a shared computer.
- **Lost the phone and the codes?** Whoever has a shell on the server can remove the second
  factor from an account:

  ```sh
  uv run manage.py mfa_reset alex.morgan
  ```

  A password alone signs in again; set it up afresh afterwards.

Capture tokens are their own credential and do not go through the second factor: a
browser extension has no code to type. See [The capture API](The-capture-API).

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
  whoever else ends up holding the link. An invitation bound to an address also counts as
  that address's verification: the link went to that mailbox, and following it is the
  same proof a verification link would give, so the invited person is not asked twice.

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
uv run manage.py changepassword alex.morgan     # the username, not the address
```
