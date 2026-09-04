# Postulo

**A self-hosted job application manager, written from the applicant's side of the table.**

Every applicant tracking system on the market is built for the company doing the hiring.
Postulo is built for the person applying: your applications, your CVs, your cover
letters, and the record of what actually happened, on hardware you control.

From the Latin *postulō* — "I apply for". First person, deliberately.

> **Status: 0.1.0.** Usable, and used — but by one person, for days rather than months.
> Treat it as a first release that works rather than as a mature one. See
> [Roadmap](Roadmap).

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
