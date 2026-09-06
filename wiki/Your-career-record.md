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

## Identifiers

A name is not an identity. Two researchers share one, one researcher publishes under three,
and a marriage or a transliteration turns one into another. An **ORCID** says which
researcher you are regardless, and in academia it is what an application form asks for by
name — so *Your details* takes one, along with a ResearcherID, a Scopus Author ID, an ISNI,
or anything else under *Other* with a name you give it.

Paste the whole address if that is what you have; Postulo keeps the identifier. An ORCID's
last character is a checksum, so a mistyped one is refused with the reason. Nothing is ever
looked up: Postulo asks orcid.org nothing, and the checksum catches what a lookup would.

Identifiers appear in a CV's contact block beside your website, on the variants where you
have asked for contact details.

## Importing a Europass CV

**Documents → Your career → Import**

If you have applied to an EU institution, or through a national employment service, you
already have a Europass CV. Typing that career record in a second time is exactly the work
Postulo exists to remove, so it will read the file instead.

Postulo reads **both** Europass formats:

- the **JSON**, which is what europass.europa.eu exports today;
- the **XML**, which is what the old CV editor produced and what an older export sitting on
  your disk still is. It does not matter which namespace the file carries — Europass has
  been through several over the years, and all of them read.

You do not have to know which one you have. There is one file box; Postulo works out which
format it was handed and tells you on the review page.

### It happens in two steps

1. **Choose the file.** Postulo reads it and shows you what it found: how many positions,
   qualifications, languages, skills and projects, and which personal details are in it.
2. **Press the button.** Nothing reaches your career record until you have seen the list
   and confirmed it.

Between the two steps Postulo holds the *parsed record*, not your file. There is no reason
to keep somebody's CV on a server for longer than it takes to read it.

### What it will and will not do

**An import only ever adds.** Nothing already in your career record is changed or removed.
If you import the same file twice you will have everything twice, and deleting the copies
is a minute's work — losing something you had written is not.

Blank fields on *Your details* are filled in: headline, telephone, where you live, your
website, and your name if you have not given one. **A field that already says something is
left exactly as it is.** Your own words about yourself beat a form you filled in years ago.

A skill heading you already have — *Digital*, say — is used rather than repeated, so you do
not end up with the same heading twice.

If one of the websites in the file is an **ORCID** address, it is lifted out and kept as an
identifier rather than as another link — that is what an academic application form asks for
by name. Its checksum decides: a mistyped one is dropped rather than saved, and orcid.org
is asked nothing. An ORCID you already have is left as it is.

### A file that is only half right

An export can be missing pieces, or carry a section in a shape Postulo does not recognise.
That is not a reason to refuse the lot. The half that reads is imported, and the review page
lists **what could not be read** before you confirm, so nothing goes missing quietly. After
the import, anything that was read but not written — a job with no start date, which there
is nothing to order — is named in a message rather than dropped.

A date is treated the same way. A month or a day that is simply **absent** becomes the first
of the period, because people write "2019" and mean it. A month that is **present and
unreadable** produces no date at all: turning nonsense into January could misdate a job by
eleven months, and being told is better than being wrong.

### Languages lose four levels of five

Europass records listening, reading, conversation, speaking and writing separately, and
almost nobody is the same at all five. Postulo keeps one level per language, so it takes
**the lowest**. Claiming the best of five on a CV is the kind of thing that gets found out
in an interview. The review page shows you all five before you confirm, so you can correct
it afterwards knowing exactly what was set aside.

### What is refused

The file came from somewhere else, so it is read carefully. There is a 5 MB cap for both
formats. Any XML carrying a **document type declaration** is refused outright without being
parsed: that is where entity expansion lives — the "billion laughs" attack and external
entity fetches both need one — and a genuine Europass export has no use for it. JSON that
nests more than forty levels deep is refused too, because a career record is not that deep.
Nothing is fetched while reading: no schema is resolved and no network request is made.

A file that is neither format, that does not parse, or that has no `LearnerInfo` section is
refused with the reason rather than half-imported.

### From the command line

An operator with a shell can do the same thing:

```sh
python manage.py import_europass cv.json --user alex@example.org --dry-run
```

Either format, and the file decides which: the command says which one it read.

`--dry-run` says what it found and writes nothing. Without it, the import runs and reports
what it added.

## Ordering

Each entry has an **order** number; lower numbers come first, and the arrows on the
overview page nudge an entry up or down.

Experience and education are additionally sorted by date, newest first, which is what a
CV usually wants. Order breaks ties.

## Previewing

**Preview** shows everything you have written, in one page, as continuous prose. It is
not a CV — it has no selection and no theme. It is there so you can read what you have
and spot the gaps.
