# Tracking applications

An application begins as a listing — a posting you noticed — and becomes an application
the moment you apply to it. That first stage has its own page: [Listings](Listings). This
one is about what happens from the application onwards.

## Statuses

| Status | What it means |
| --- | --- |
| **Draft** | You are considering it. Nothing has been sent. |
| **Applied** | You sent something. The clock starts here. |
| **Acknowledged** | They confirmed receipt — a human or an autoresponder. |
| **Screening** | An initial call or a recruiter conversation. |
| **Interviewing** | Interviews proper. |
| **Assessment** | A take-home task, a test, or a technical exercise. |
| **Offer** | An offer is on the table. |
| **Accepted** | You took it. |
| **Rejected** | They said no. |
| **Withdrawn** | You pulled out. |
| **Ghosted** | They stopped replying. |

The first seven count as *still live*. The last four are settled.

**Ghosted is deliberately not the same as rejected.** An employer that stops replying has
not made a decision you were told about, and recording it as a rejection would misstate
both their behaviour and your own response rate. It is by far the most common ending, and
it deserves its own name.

Nothing is ever deleted by changing a status. Rejections and withdrawals are exactly the
records that make the interesting questions answerable.

## Two views of the same thing

- **Board** shows only live applications, in columns by status. Move one along with the
  dropdown on its card — it saves immediately.
- **Applications** is the full table, including settled ones, with search and filters.

Both share the same filters: text search across job title, company and location; status;
outcome (live or settled); and tag.

## The timeline

Every application has an append-only log of what happened. Status changes are recorded
automatically, including when you change the status from the edit form — there is no way
to move an application without the log noticing.

You can add entries yourself: a call, an email sent or received, an interview, an
assessment, a follow-up, or a plain note. Each carries the date it happened, which may be
different from the date you got round to recording it.

The status field is what the board and filters read. The log is what actually happened.
When they disagree, believe the log.

## Interviews

A timeline entry says an interview *happened*. An interview in the diary says one *will*:
it has a start and an end, a place or a link to the call, the people you are meeting, a
kind — phone screen, video call, on site, panel, assessment — and notes to prepare with.

Schedule one from the **Interviews** card on the application page. Three things follow:

- the timeline records that it was scheduled, and later how it went;
- a reminder falls due the day before (untick it if you do not want one), so whatever
  tells you about reminders tells you about interviews too;
- it appears under **Coming up** on the dashboard, on the board card, and in the table.

Once its time has passed, the diary asks how it went. **Held** writes an *interview* entry
on the timeline dated when it took place and moves the status forward if it had not kept
up — a phone screen to *Screening*, an assessment to *Assessment*, anything else to
*Interviewing* — through the same path as any status change, so the log says so. It never
moves a settled application: an interview you remember after a rejection reopens nothing.
**Cancelled** and **No-show** are recorded too; nothing is deleted.

Recording one that already happened uses the same form: an interview whose time is past is
written straight onto the timeline as held.

Every interview has a **Calendar** link that downloads an `.ics` file any calendar
application imports, and **Dashboard → All interviews → Calendar file** downloads everything
still ahead in one file. Each interview keeps a stable identifier, so importing the file
again updates the meeting rather than duplicating it.

## Reminders

Reminders are a note plus a date. Overdue ones are highlighted, and anything due appears
on the dashboard.

A reminder appears in the application and on your dashboard when its time comes. To be
*told* — by email, or by whatever a notification plugin speaks — add a connection under
**Settings → Connections** and tick *A reminder falls due*. The built-in **Email** notifier
needs only an address and the instance's mail settings; the operator must also run the
scheduler that notices reminders falling due (see
[Configuration](Configuration#notifications)). Each reminder is announced once.

## Tags

Free-form labels of your own invention — `remote`, `dream job`, `via Marie`, whatever
fits how you think. Manage them under **Dashboard → Manage tags**, and filter by them
anywhere.

## Companies and people

Companies are created for you when you record an application. Opening one shows every
posting you have recorded there and every application you have made.

Contacts are the people: recruiters, hiring managers, a friend on the inside. Add them
from a company page. An application can name one of them as its main contact.

Companies are private to your account. Two people sharing an instance each keep their own
record of the same employer, and neither can see the other's opinion of them.

## Not retyping adverts

Paste a posting's address and Postulo will read the page for you — see
[Capturing postings](Capturing-postings). What it reads lands in your
[Listings](Listings), and applying from there is one short form.

## What the record adds up to

**Insights** turns the timeline into figures: how far applications get, what share are
answered at all, how long employers take, and which sources actually convert. See
[Insights](Insights).
