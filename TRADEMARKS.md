# Trademarks

Postulo's **code** is Copyright (C) 2026 Postulo contributors, [AGPL-3.0-or-later](LICENSE).
Postulo's **name and logo** are not, and this file says what that means — for anybody forking
it, for anybody writing a plugin, and for the marks belonging to other people that appear in
this repository. The third-party *code and artwork* shipped here, and the copyright licences
they come under, are a different matter and have their own register:
[THIRD-PARTY.md](THIRD-PARTY.md).

Nothing here restricts the licence. AGPL-3.0 §7 lists the terms a work may add, and clause
(e) is *"Declining to grant rights under trademark law for use of some trade names,
trademarks, or service marks"*. Reserving a name is the licence's own provision, not
something bolted onto it.

## Postulo's name and logo

The name **Postulo** and the logo in [`assets/brand/`](assets/brand/) belong to the project.
They are the one thing the licence does not hand over, and there is a reason for it that is
worth stating rather than assuming.

The README makes four promises — never paywalled, secure, usable by everyone, modular. A
licence cannot enforce a promise. Someone could fork this, put a feature behind payment, and
call it Postulo, and everybody who had heard the promise would have been told something
untrue. The name is the only instrument that speaks to that, and it is reserved for that
reason rather than for control.

### You do not need to ask

- **Run it, anywhere, for anything.** No permission, no notification, no registration.
- **Fork it, study it, change it, and share your changes.** That is the licence, and nothing
  here narrows it.
- **Redistribute Postulo unmodified under its own name**, including packaging it for a
  distribution or building your own container image of an unmodified tree.
- **Say what is true.** "Runs on Postulo", "a Postulo plugin", "imports into Postulo",
  "compatible with Postulo", "a guide to Postulo". Naming software to say what your thing
  works with is ordinary and welcome.
- **Name a plugin `postulo-something`.** This is the project's own convention —
  `postulo-apprise`, `postulo-paperless`, `postulo-helloworld` — and it is how anybody
  finds a plugin. It carries no claim of endorsement and needs no permission.
- **Call your instance whatever you like.** Postulo has a setting for exactly this, because
  an instance is yours and should say your name and not the software's.
- **Write about it, criticise it, teach it, screenshot it.**

### Where to use another name

If you distribute a **materially modified** Postulo — different features, different
promises, a different idea of what it is for — give it another name, or say plainly and
near the name that it is a modified version and not endorsed by this project.

This is not about patches. Fixing a bug, adding a translation, carrying a small change for
your own instance: still Postulo. It is about the case where somebody installing your thing
would reasonably think they were getting this one, and would be wrong.

If you are unsure, ask: <tiago.agueda@tiagoagueda.com>. The answer is nearly always yes.

## Marks belonging to other people

Postulo displays and, in a few cases, ships artwork it does not own. **All trademarks are
the property of their respective owners.** They appear here to identify the thing they
belong to — a format Postulo reads, a service a plugin connects to, a page a link goes to —
and never to suggest that their owners have endorsed, reviewed or are involved with this
project. They are not.

### In this repository now

| What | Where | Whose | Under |
| --- | --- | --- | --- |
| Brand marks of Bluesky, Codeberg, Facebook, Forgejo, GitHub, Instagram, Mastodon, ORCID, SourceHut and YouTube | `src/postulo/static/brands/` | Their owners, via [Simple Icons](https://simpleicons.org) | Shown beside a link to say which service it goes to, unmodified and in one of the two modes the rule below allows (the owner's published colour, or black and white where the owner's guidelines say so). The notice beside them names each owner and says no endorsement is claimed. |
| Buy Me a Coffee banner and QR code | `assets/support/` | Buy Me a Coffee | Both published by Buy Me a Coffee for people to use, and used to link to the project's own account. The code is scaled and nothing else. See the notice beside them. |

That is a mark, which is not licensed at all — hence this file. The works shipped here
under a copyright licence — the Lucide icons, the flag-icons artwork, Tailwind CSS,
basecoat-css, htmx and zxcvbn — are satisfied by carrying their notices, and
[THIRD-PARTY.md](THIRD-PARTY.md) says where each notice is.

**National flags** are a category of their own. Some countries regulate how their flag may be
used. Postulo shows them at about twenty pixels beside a language name, unmodified, to help
somebody find their own language in a list. If that is a problem in your jurisdiction, the
flag for a language is one entry in `core/languages.py` and Postulo already renders nothing
where a language has no uncontested home.

### Named in the documentation and the interface

Postulo talks about the software it runs on and the services its plugins reach: Django,
Python, Docker, PostgreSQL, SQLite, WeasyPrint, WhiteNoise, Playwright, Prometheus, Traefik,
Forgejo, GitHub, Europass, Apprise, Paperless, Nextcloud, Telegram, Signal, Matrix, Discord,
ntfy, Gotify, Pushover, CalDAV, LinkedIn, ORCID, and others. Those names belong to their
owners and are used to say what Postulo works with. Tailwind CSS, htmx and zxcvbn are not in
this list, because their code is in this tree rather than merely mentioned; they are in
[THIRD-PARTY.md](THIRD-PARTY.md).

## The rule for plugin logos

A plugin may carry a logo, and that logo is often somebody else's mark — the service it
connects to, the format it reads. It may be shipped with the plugin when **all four** hold:

1. **It identifies something the plugin actually works with.** The format, the service, the
   thing at the other end. Never decoration, and never a badge of quality.
2. **It is unmodified.** Not recoloured to match a theme, not redrawn, not composed into
   something else. Postulo has opinions about its own palette and none about anybody else's.
   The one exception is a colour the owner's own guidelines name: see below.
3. **A notice travels with the file**, naming the owner and stating that no endorsement is
   claimed — the arrangement `src/postulo/static/flags/LICENSE.txt` already uses, for a
   different reason.
4. **It sits outside the licence grant**, in its own directory, with that stated. The AGPL
   covers the code and cannot speak for somebody else's mark, and every fork redistributes
   whatever is in the tree.

The same rule binds the marks in Postulo's own interface (`src/postulo/static/brands/`),
and each mark there has one recorded mode, in `NOTICE.txt` beside it:

- **brand**: the owner's published colour, unmodified, and only where that colour clears
  3:1 on both the light and the dark page. Where it does not, the mark is not drawn in it.
- **single colour**: drawn in `currentColor`, which the stylesheet makes exactly black on
  the light page and exactly white on the dark one. This is the only case where rule 2
  lets a mark take a colour other than its published one, and it is permitted **only
  where the owner's own published guidelines allow a one-colour version in those colours**
  (ORCID's say the iD icon may be the high-contrast black and white icon, or the
  reversed-out white one on a darker background, and no other colourway; GitHub's say the
  logo should appear only in white, black or, in a few cases, grey or green; Forgejo
  publishes a monochrome logo and SourceHut one in black and one in white). The page that
  says so, and its words, are recorded in `assets/brands.txt` and in `NOTICE.txt`, and
  `npm run sync:brands` refuses a single-colour mark without them. Black and white are
  used because they are the colours those guidelines name and the two that clear 3:1 on
  the page they are drawn on; it is not a licence to match a theme's accent.
- **none**: a mark whose published colour fails 3:1 on a page and whose owner's guidelines
  were not shown to allow a one-colour version is not shipped, and the service shows its
  generic icon. A guess about what an owner allows does not count: X, Threads, Xing,
  Bitbucket and GitLab are in this case, and so is Wikidata, whose official logo is three
  coloured bars: Simple Icons' flat one-colour version would be a recolouring, which
  the Wikimedia trademark policy does not allow without separate permission.

LinkedIn has no mark here, and none is taken from elsewhere: the one source Postulo
weighed dropped it on the strength of LinkedIn's own terms.

Where any of the four fails, the plugin shows the neutral fallback and its own name does the
identifying. A plugin with no logo is not a broken plugin.

**A logo is not a badge.** Postulo may mark a plugin as coming from a signed catalogue; that
says *this file is the one we published*, and says nothing about whether the service whose
logo sits beside it has ever heard of it. The two should not be made to look like one claim.

## If you own a mark used here

If something on this page is wrong, or you would rather your mark were not used this way,
write to <tiago.agueda@tiagoagueda.com> and it will be changed. That is a shorter route than
a letter from a lawyer and it will be taken just as seriously.
