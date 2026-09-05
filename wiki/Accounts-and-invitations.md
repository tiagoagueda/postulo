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

## Single sign-on

An instance can sign people in through an **OpenID Connect** identity provider —
Authentik, Keycloak, Pocket ID, Zitadel, Kanidm, Google, or anything else that speaks it.
It is native: nothing to install, four variables in the environment (see
[Configuration](Configuration#single-sign-on)), and a button on the sign-in page named
after the provider.

- **Existing accounts link, they are not duplicated.** An address the provider has
  verified signs in the account that holds it, and the provider is connected to that
  account from then on. People can see and disconnect it under *Settings → Account →
  Sign-in methods*.
- **By default, existing accounts only.** The identity provider is not an invitation:
  someone it knows but Postulo does not is turned away, unless they followed an
  invitation link or registration is open. Set `POSTULO_OIDC_AUTO_SIGNUP=true` to let
  the provider create accounts — right for a household or a small team where it already
  gates everything.
- **The username and name come from the provider's claims** — `preferred_username`,
  `given_name`, `family_name` — bent to Postulo's rules, with the address's local part
  as the fallback for the username. An address the provider says it verified needs no
  further click.
- **Two-factor still applies** after a single sign-on, if the person has it on.
- **The callback address** the provider must be told is shown under *Server settings →
  Sign-in*, exactly as the browser reaches it: scheme, host and port must match.

Sign-in tokens from the provider are not stored; Postulo has no use for them.

## Administrators

An administrator runs the instance: invites people, sees the list of accounts, and
changes the instance's policy under **Server settings**, reached from the account menu
(top right). Administrators see *accounts* — never anyone's applications, documents or
contacts; those stay private to the person who owns them.

**The first account is the administrator.** On an empty instance the sign-up form is
offered to whoever reaches it, and the account it creates administers the instance; its
address is trusted, since nobody else exists yet to send a verification link. After that
the door closes again. `createsuperuser` on the command line still works, and does the
same thing.

*Server settings → People* lists every account and lets an administrator make or unmake
other administrators and deactivate accounts (nothing deleted, no sign-in). The last
administrator cannot be removed or deactivated, and nobody can deactivate the account
they are signed in with.

## Who can sign up

Registration is closed by default: the sign-up form is not offered, and the only way in
is an invitation. An instance holding somebody's employment history has no reason to
accept strangers.

An administrator opens it under *Server settings → Sign-in*. The operator may also pin it
with `POSTULO_REGISTRATION_OPEN` in the environment, in which case the environment wins
and the page says so. Open it only if you genuinely want anyone who finds the URL to be
able to create an account.

## Invitations

**Server settings → People → Invitations.**

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

Only administrators can issue invitations. To make someone an administrator, use *Server
settings → People*.

## Separation between accounts

Every record in Postulo belongs to exactly one person, and every query is scoped to its
owner. Companies, postings, applications, tags, CVs, letters and files are all private to
the account that created them. Two people on the same instance can each track the same
employer without ever seeing the other's notes.

Asking for a record belonging to someone else returns **not found** rather than
**forbidden** — confirming that a record exists would itself disclose something.

## The administration interface

Everything an administrator needs day to day is under **Server settings**: an overview
of what is running and where the data is, the accounts, the sign-in policy, a test of
the email settings, the installed plugins, capture policy, and the instance's name and
the defaults new accounts start with.

Django's own admin remains, as the escape hatch, by default at `/admin/`. On a public
instance, move it:

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
