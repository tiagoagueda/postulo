# Reports

**A report about a period**, at *Dashboard → A report on a period*, or `/applications/report/`.

Two different people ask for this and they want the same document.

The first is **an employment office**. Unemployment benefit in most of Europe is conditional
on actually looking — a minimum number of applications in a period, evidenced. Postulo
already holds every one of those facts, and copying them out by hand is precisely the work
that makes people stop keeping records at all.

The second is **you**. A month of looking feels like nothing happened. A page saying *eleven
applications across four weeks, none in the week of the 12th* is the difference between a
feeling and a fact, and it says where the gap was.

## Choosing the period

A **month**, a **quarter**, **the last few weeks**, or **dates you choose**. Everything on
the page is scoped to it, and nothing from outside it is ever counted.

The month box serves both a month and a quarter: choose *a quarter* beside September 2026
and you get July to September. *The one before* and *the one after* step by a whole period —
a month before a month, a quarter before a quarter, and a range you gave by hand moves by
its own length so it stays the same size.

There is never a link into the future. A report about next month is a blank page pretending
to be a document.

**The period is in the address**, which means a report for a particular month is a thing to
bookmark, to send to somebody, and to ask for again next year and get the same page back.
That is the same line Postulo draws everywhere: a *question* lives in the URL, a
*preference* lives on your profile.

## How regularly

Regularity is what the report is named for, so it is the top of the page.

- **Sent** — applications sent in the period.
- **A week, on average** — that number divided by the weeks the period covers.
- **Longest gap** — the longest run of days inside the period with nothing sent.
- **Weeks in a row, up to now** — weeks with at least one application, counting back from
  the last week. If the most recent week has none, this is zero, and it says zero.

Then a bar per week, with the weeks that have none saying **none** in words rather than
being an invisible gap.

**Two things about the gap, both deliberate.** It is measured *inside* the period, so a
silence that began in March is not carried into April's report; and a period that has not
finished is only counted up to today, because the rest of the month is not a gap anybody has
had yet.

A week marked **part of this week is outside the period** straddles one end of it — a month
rarely begins on the first day of a week. Nothing from outside the period is counted in it;
the mark is there because a bar that is short because the week was short would otherwise
mislead.

Weeks run from whichever day the language you are reading Postulo in starts a week on.

## What came back

**Replies, interviews attended, offers and rejections**, counted by *when they happened*.

That is a different question from what was sent, and the two are kept apart rather than
added together. A reply arriving in September to an August application is September
activity — it belongs in September's report even though the application does not. Folding
the two together would give a figure that is true of neither question.

**One reply per application, not one per step.** An application acknowledged, then screened,
then interviewed in the same month had one reply; counting each step would report a busier
month than happened.

## Every application, in order

The evidence: date applied, company, role, where it was found, the address of the posting,
the current status, and the date of the last thing that happened. One row per application,
oldest first.

**Applications drafted but never sent are not in it**, and the page says so. An employment
office is asking what was sent. If there are drafts in the period, the page says how many
are being left out, so the absence is stated rather than silent.

## Out as a document

**Download PDF** gives a document to hand over. It carries your name, the period and the day
it was produced, because a document with no date is not evidence of anything. It is laid out
for A4 with page numbers, and it lays out from the other edge in a right-to-left language.

**Download CSV** gives the evidence list as a spreadsheet, for anybody who wants to do their
own sums. Only the list — a cadence is a reading of those rows rather than a separate fact,
so a spreadsheet holding them can produce its own.

Both take the period from the page you are looking at.

> **A PDF needs a renderer.** Postulo uses WeasyPrint by default and can use headless
> Chromium instead; if neither is installed the page says so plainly rather than failing.
> See [Configuration](Configuration) for `POSTULO_PDF_BACKEND`. The CSV needs nothing.

## What Postulo will not do

**It sets no target.** There is no "expected" or "recommended" number of applications
anywhere on the page. How many a period should hold is a benefit regime's rule or your own,
never this software's. If you want to record what you were told to do, that is a number you
set, not one Postulo invents.

**It has no template per country.** IEFP, France Travail and the Agentur für Arbeit each
want a list of what was applied for, where, when and what came of it, and one honest report
answers all three. What a particular office asks for is worth checking with that office
rather than being guessed at in software.

**It is produced on request, not on a schedule.** A monthly report arriving on the first of
the month is an obvious thing to want once a scheduler exists, and it is a separate piece of
work; getting the page right came first.

## Where the numbers come from

The same place as the dashboard's: **the event log, not current statuses**. An application
that reached an interview and was then rejected has a current status of *rejected*; counting
current statuses would say you had no interviews. See
[Insights and the dashboard](Insights#read-from-the-timeline-not-from-statuses).

Nothing is stored. A report is computed when you ask for it, from records that are already
the truth — which is what makes it a snapshot of the record at a moment, and why "edit this
report" never means anything.
