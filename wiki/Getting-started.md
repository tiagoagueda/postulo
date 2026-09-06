# Getting started

This walks through the first half hour, in the order that makes the least work.

## 1. Sign in

On a fresh instance, open the site and create the first account: it becomes the
administrator. (Or use the one you created with `createsuperuser`.) Either the username
or the email address signs in. The address counts as verified for that first account; for
everyone who signs up afterwards, a link is sent to their address and they sign in once
they have followed it.

## 2. Fill in your details

**Your name, top right → Your details.** The menu under your name also holds *Settings*,
*Export everything* and *Sign out*.

*Your details* is the contact block that gets printed at the top of your CVs: your name,
headline, phone, location and links. It is worth doing first, because every CV you
generate reads from it.

How Postulo behaves for you lives under **Settings**, one section per page:

- **Appearance** — light, dark, or match your operating system. The switch at the top
  right of every page cycles through the same three; this is the explicit version.
- **Language and time** — the interface language, and the time zone dates are shown in.
  Postulo speaks every official language of the European Union; a language whose
  translation is a machine-assisted draft says so in the list until a speaker has
  reviewed it, and [translating](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/docs/TRANSLATING.md)
  is the easiest way to help.
- **Account** — your username, your email addresses, your password, two-factor
  authentication, and any single sign-on connected to the account.
- **Connections** — where the plugins that act for you (notifications, document stores,
  synchronisation) find their services and how they sign in. Empty until the operator
  installs such a plugin; each one you add is tested, run and reported on from here.
- **API tokens** — for the browser extension and anything else that acts for you, each
  allowed only what you tick (see [The API](The-capture-API)).
- **Your data** — the export.

## 3. Note what you find, then apply

Postings you notice go into **Listings** first: capture one from its address, or **Add a
listing** by hand. Only the company and the job title are required; the company is
matched by name and created for you if it is new, so you never have to set one up first.
From there, each listing is shortlisted, discarded, or applied to — and **Apply** is what
turns it into an application with a timeline. See [Listings](Listings).

Already applied somewhere? **Record an application**, on the dashboard or on the
Applications page, does both steps in one form.

Two fields are worth filling in even when you are in a hurry:

- **The posting text.** Paste it. Postings vanish from the web the moment they are
  filled, and by the time you get an interview the advert you applied to may no longer
  exist anywhere.
- **Status.** Set it to *Applied* if you have already sent something, or leave it on
  *Draft* if you are still deciding.

That is the minimum useful loop. Everything below makes it more valuable.

## 4. Write your career down once

**Documents → Your career.**

Add your roles, qualifications and skills here rather than in a document. It takes a
while the first time and never again: every CV variant you build afterwards selects from
this record, so fixing a job title fixes it everywhere.

See [Your career record](Your-career-record).

## 5. Build a CV variant

**Documents → New CV.**

A variant is a *selection* from your career record, in an order you choose, with
optional per-variant rewording. Make one per kind of role you apply for — not one per
application.

See [CVs](CVs).

## 6. Record what you sent

On an application, **Documents → Record what you sent**.

Postulo renders your chosen CV and cover letter as they stand at that moment and keeps
those PDFs unchanged forever. This is the feature you will not appreciate until three
months later, when someone asks about a line on a CV you have since rewritten.

See [Files and what you sent](Files-and-what-you-sent).

## Seeing it with data first

An empty Postulo is hard to judge. To fill an account with a fictional but believable
search — thirty applications over six months, ending every way a search can, with CVs,
letters and PDFs of what was "sent":

```sh
uv run manage.py seed_demo you@example.org
```

Everything it creates is obviously invented (the employers are Aperture Science, Black
Mesa and their friends), and the timelines are scripted so that Insights has a real story
to tell. It refuses to add to an account that already holds a search; pass `--reset` to
replace one, or give it a different address and `--password` to create a throwaway
account. With Docker: `docker compose exec postulo python manage.py seed_demo …`.

## Where things live

| Page | What it is for |
| --- | --- |
| **Dashboard** | What needs doing: applications worth chasing, reminders due, recent activity. |
| **Applications** | Everything, as a filterable table. |
| **Board** | Only what is still live, arranged by status. |
| **Documents** | CVs, cover letters, uploaded files, and your career record. |
| **Companies** | Employers, their people, the postings you recorded, and a logo if you gave one. |
| **Reminders** | Things to chase, with dates. |
