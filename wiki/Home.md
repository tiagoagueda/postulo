# Postulo

**A self-hosted job application manager, written from the applicant's side of the table.**

Every applicant tracking system on the market is built for the company doing the hiring.
Postulo is built for the person applying: your applications, your CVs, your cover
letters, and the record of what actually happened, on hardware you control.

From the Latin *postulō* — "I apply for". First person, deliberately.

> **Status: 0.1.0.** Usable, and used — but by one person, for days rather than months.
> Treat it as a first release that works rather than as a mature one. See
> [Roadmap](Roadmap).

## Never paywalled

**No feature of Postulo is, or ever will be, behind a paywall.** People looking for work
are, more often than not, people who cannot afford to pay for the tools to find it.
Everything this software does is available in full to everyone who runs it — no paid
tier, no "pro" edition, no licence key, no feature that unlocks later. That is a
commitment, not a strategy, and it does not change.

## Modular by design

Anything that could reasonably vary sits behind an interface that a separately installed
package can implement — where a posting is read from today; how you are notified and how
documents are rendered next. Postulo's own implementations are plugins that happen to
ship in the box, and a plugin written by somebody else is installed with one command and
no change to Postulo. The person who cares about a particular job board, or a particular
way of being notified, should never have to wait for this project.

## Secure, because of what it holds

**Postulo holds the most personal documents a person has while looking for work, and it
is built as if that were the only thing that mattered.** A CV carries a home address, a
phone number and a full employment history; a timeline says who has and has not replied.
So the code and the data are kept as secure as the project knows how to make them: every
record is owned and every query is scoped, files are never served without a permission
check, the browser is held to a strict content security policy, nothing is fetched from
the network without a deliberate decision, and the test suite includes security tests —
ownership sweeps, policy checks, the things an attacker would try — that fail the build.
Dependencies are checked for known vulnerabilities on every run and on a schedule, so a
fresh disclosure is noticed without anyone having to remember to look. A security report
is handled before any feature is; see the [security policy](https://source.tiagoagueda.com/tiagoagueda/postulo/src/branch/main/SECURITY.md).

## Built for everyone

**Postulo is meant to be usable by everyone, at its fullest, including people with
disabilities.** Looking for work is hard enough without the tool getting in the way. So
the interface is server-rendered HTML that works with scripts off, every control can be
reached and operated from the keyboard, images and icons carry names or are marked as
decorative, colour never carries a meaning on its own, changes on the page are announced
to screen readers, and the pages are checked against the accessibility guidelines
(WCAG 2.2, level AA) as part of the browser tests rather than as an afterthought. When a
feature cannot be made to work for someone, that is a bug, and it is filed as one.

## Start here

| If you want to… | Read |
| --- | --- |
| Put Postulo on a server | [Installing Postulo](Installing-Postulo) |
| Understand a setting | [Configuration](Configuration) |
| Use it for the first time | [Getting started](Getting-started) |
| Keep on top of applications | [Tracking applications](Tracking-applications) |
| Stop retyping adverts | [Capturing postings](Capturing-postings) |
| See what the record adds up to | [Insights](Insights) |
| Write your CV once and tailor it | [Your career record](Your-career-record) and [CVs](CVs) |
| Reuse a cover letter properly | [Cover letters](Cover-letters) |
| Know what you sent to whom | [Files and what you sent](Files-and-what-you-sent) |
| Share the instance with someone | [Accounts and invitations](Accounts-and-invitations) |
| Not lose everything | [Backups and your data](Backups-and-your-data) |
| Capture from a script or extension | [The capture API](The-capture-API) |
| Fix something | [Troubleshooting](Troubleshooting) |

## What it does

- **Tracks applications** from a posting you spotted through to an offer, with an
  append-only timeline that records what happened and when.
- **Holds your career once** — every role, qualification and skill written down a single
  time, with CV variants selecting from it rather than copying it.
- **Tailors CVs** per role without forking your history.
- **Keeps what you actually sent**, frozen as a PDF at the moment of sending.
- **Stores files you already had**, versioned, and never serves them publicly.
- **Tells you what is working** — how far applications get, how long employers take,
  which sources convert — read from the timeline rather than from current statuses.
- **Reads a posting from its address**, and asks you to confirm it before recording
  anything.

## What it will not do

- Apply to jobs for you.
- Send anything anywhere on your behalf. Postulo records what you sent; you still send it.
- Phone home. There is no telemetry, and no outbound request Postulo makes on its own.
- Charge you for any of it, now or later.
