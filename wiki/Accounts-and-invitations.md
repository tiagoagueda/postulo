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

## Your picture

The header shows your initials on a coloured tile until you give it a picture, under
**Your details → Your picture**. Two ways, in this order of precedence:

- **Upload one.** PNG, JPEG, WebP or GIF up to 5 MB. Postulo decodes it, straightens it,
  cuts it to a square and re-encodes it, which also strips everything the file knew about
  where and when it was taken — a phone photograph carries the place. It is stored under
  private media and served only to you (and to administrators), never by the web server.
- **Use my Gravatar.** Tick the box and Postulo fetches the picture for your primary
  address from gravatar.com **once, from the server**, keeps a copy and shows that. Pages
  never point your browser at Gravatar: an image loaded from there would tell them your
  address and a hash of your email on every page view. It is fetched again when your
  primary address changes, or when you press *Fetch my Gravatar again*. Untick the box and
  the copy is deleted. If Gravatar has no picture for your address, the initials stay and
  the page says so.

Nothing here appears on a CV. Whether a photograph belongs on one depends on the country,
and that will be a per-CV choice, off by default, when it arrives.

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

API tokens are their own credential and do not go through the second factor: a
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
  Sign-in methods*. Only a **verified** address ever matches; an unverified claim links to
  nothing. That does mean the instance takes your provider's word for what verified means,
  so if you do not run the provider, read the note on [Hardening](Hardening#single-sign-on-and-what-it-asks-you-to-trust)
  and consider `POSTULO_OIDC_LINK_BY_EMAIL=false`, which makes each person connect the
  provider from their own account page instead.
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
other administrators, deactivate accounts (nothing deleted, no sign-in), delete an account
outright (see *Deleting your account*) and change a person's username on their behalf. The
last administrator cannot be removed, deactivated or deleted — by anyone, including
themselves — and nobody can deactivate or delete the account they are signed in with from
that page. A username is unique across the instance whoever changes it: a name
already in use, in any capitalisation, is refused before anything is saved.

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

## Deleting your account

**Settings → Your data → Delete my account.** The page says exactly what goes —
applications, listings, companies and contacts, documents *and the files behind them on
the disk*, reminders, interviews, tokens, connections and their secrets, the invitations
you issued that nobody accepted, the account itself — and offers the export first,
prominently. Confirming takes your password again (or your second factor) and your address
typed out. Then it is done, at once: there is no grace period, because a "pending
deletion" state is a second thing to get wrong, and the export is the safety net.

The one account that cannot be deleted is the last administrator's, by anyone, including
themselves: make somebody else an administrator first. An administrator can delete another
person's account from *Server settings → People* (the same service, the same file cleanup),
and an operator from the command line:

```sh
uv run manage.py delete_account alex.morgan     # asks first; --yes to skip the question
```

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

A new password must be at least twelve characters, not all digits, not on the list of
commonly used passwords, and not too close to your own name or address. Wherever you
choose one — sign-up, an invitation, *Change password*, *Set password* for an account
that came in through single sign-on, a reset link — a meter under the field says how
strong it is as you type: *very weak* to *strong*, with a hint when it is low. The estimate
is made in your browser (by zxcvbn, which knows about dictionary words, keyboard walks,
dates and repeats, and about the name and address you typed above) and the password never
leaves your browser until you submit the form. The rules listed under the field are what
the server checks; the meter cannot contradict them. Nothing appears on the sign-in form.

Password resets are sent by email, so they need working email settings — see
[Configuration](Configuration#email). Without them, reset a password from the command
line:

```sh
uv run manage.py changepassword alex.morgan     # the username, not the address
```
