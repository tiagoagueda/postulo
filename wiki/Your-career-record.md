# Your career record

**Documents → Your career**

This is the master copy: every role you have held, every qualification, every skill,
written down once. CV variants select from it. They never copy it, so correcting a job
title here corrects it on every CV you have.

## What it holds

| Section | Notes |
| --- | --- |
| **Experience** | Roles you have held. Leave the end date empty for your current one. |
| **Education** | Qualifications, finished or in progress. |
| **Projects** | Things you built that are worth showing. |
| **Skills** | Grouped under headings you invent, such as *Languages* or *Infrastructure*. |
| **Certifications** | With issuer, dates and a credential link. |
| **Languages** | Spoken languages, rated on the CEFR scale (A1 to C2, or native). |

The organisations you have worked for are kept separately from the companies you are
applying to. They are unrelated lists, and joining them would put former employers into
the company picker for new applications.

## Highlights, one per line

Experience, education and projects each have a **highlights** field. Write one
achievement per line — no bullet characters, no markup:

```
Cut deploy time from 40 minutes to 4.
Mentored three engineers through their first year.
Ran the migration off the legacy billing system.
```

They render as a bulleted list. Keeping them as plain lines means reordering is editing
text, and a CV variant can replace the whole set with a textarea rather than a fiddly set
of checkboxes.

## Links: portfolios, profiles, videos

**Documents → Your career record → Links**

Some of your work already lives somewhere: a portfolio, a personal site, the code you have
published, a paper, or a video of you talking for two minutes. That is an address, not a
file, so Postulo keeps it as one. A link has a title, the address, a kind — portfolio,
personal site, code, design, publication, video, other — and one line of description for
whoever is reading.

Links go on a CV like any other entry, as a **Links** section, and can be sent with an
application alongside your CV and letters; the timeline records which ones you pointed
them at.

**Video CVs** belong here too, as a link of the *video* kind: an unlisted upload on
YouTube, Vimeo, a PeerTube instance or your own share. That is what almost everyone
actually does, and it costs you nothing here. Postulo does not host video itself.

**Check it answers.** A portfolio address that returns "not found" on the day a recruiter
clicks it is the worst outcome this record exists to prevent, and Postulo cannot see it
from the inside because it never visits your links. So there is a button: *Check it
answers* on one link, *Check them all* on the section. One request each, when you press it,
and the result is kept beside the link. Nothing is ever checked on a schedule.

The check only ever visits public addresses. A link pointing at something on your own
network — `192.168.…`, `localhost`, a name that resolves to either — is reported as broken
without a request being made, and so is a public address that redirects to one. That is
true even on an instance where `POSTULO_CONNECTIONS_ALLOW_PRIVATE` is on for plugins: a
portfolio address is something a recruiter clicks from the open internet, so an answer from
inside your house would not tell you anything you wanted to know.

## Ordering

Each entry has an **order** number; lower numbers come first, and the arrows on the
overview page nudge an entry up or down.

Experience and education are additionally sorted by date, newest first, which is what a
CV usually wants. Order breaks ties.

## Previewing

**Preview** shows everything you have written, in one page, as continuous prose. It is
not a CV — it has no selection and no theme. It is there so you can read what you have
and spot the gaps.
