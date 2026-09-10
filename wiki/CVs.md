# CVs and portfolios

**Documents → CVs and portfolios**

A CV in Postulo is a **variant**: a selection of entries from [your career
record](Your-career-record), in an order you choose, with a theme.

Make one variant per *kind* of role you apply for, not one per application. Three or four
is normal. One per application means maintaining forty documents.

## A CV, or a portfolio

The same page holds both, because they are the same thing selected and laid out differently.

- A **CV** is a career read backwards: what you did, where, and when, with the dates
  carrying the argument.
- A **portfolio** is the work first — projects, and the things they point at — with the
  career following as context, set small.

Choose which on the form. Everything else is identical: the same career record, the same
tailoring, the same *included* switch, the same export. A portfolio picks from its own
family of themes, because the difference is one of structure rather than of styling.

**"Portfolios of different kinds"** — a developer's, a designer's, a researcher's — is
answered by the theme and by what you select, not by a third setting. They differ in exactly
those two things, and both are already yours to choose.

## Building one

1. **New CV or portfolio.** Give it a name for your own use — "Backend, English" — plus an
   optional headline and opening summary.
2. **Add entries.** The panel on the CV page lists everything from your career record
   that is not already on this CV, grouped by kind. Tick what belongs and add it.
3. **Order it.** The arrows move entries up and down. Sections appear in the order their
   first entry does, so moving one job to the top moves the whole Experience block with
   it.

## Tailoring without forking

This is the point of the whole design.

**Tailor** on any entry lets you rewrite its highlights *for this CV only*. The master
record is untouched, and your other variants are unaffected. Leave the override empty and
the entry follows the master copy — including any later corrections.

**Included** can be unticked to keep an entry on the variant while leaving it off the
page. Useful for something you want back next month without having to find it again.

## Themes

Two come with Postulo:

- **Plain** — a clean sans-serif layout. Sober and unremarkable, which is usually right.
- **Classic** — a serif layout with small caps and a centred contact block.

Both are A4 with generous margins, both avoid splitting a single job — or a single piece
of work — across a page break, and both set every kind of document Postulo writes,
portfolios included. A theme from a plugin only appears in the menu for the kinds it says it
can set, so a theme that has never been taught to lay out a portfolio is simply not offered
for one.

**A plugin can add more.** The menu lists whatever is installed, and a theme from a plugin
appears there under its own name with no further setting to change. A theme does not have
to set everything, though — one written for letters will simply not be offered here, and
you will see it on the letter form instead. Nothing offers you a pairing it cannot then
produce; that is the point of the menu being shorter on one page than on another.

**A theme is markup, so it arrives the way code arrives**: inside a plugin an administrator
chose to install, having seen who wrote it. There is no upload box for themes, and there
will not be one.

**A theme draws only what it carries.** The renderer fetches nothing while it draws — not a
file on the server, not an address on the network — so a stylesheet, an image or a font a
theme wants has to be inside it: its CSS inline, and anything else as a `data:` address.
One it points at by address is left out of the PDF rather than fetched, which is what keeps
a theme from reading the server's disk into somebody's CV.

**If a plugin is removed**, the CVs that used its theme keep working — they fall back to
Plain and still export. Nothing is lost except the look, and choosing another theme puts
that right.

**Contact details** are taken from your profile, not retyped per CV. Untick *include
contact details* for a variant that will be sent through a system that strips them
anyway, or for an anonymised copy.

## Previewing and exporting

- **Preview** opens the CV as HTML, exactly as the PDF renderer will see it. Fast, and
  needs no renderer installed.
- **Export PDF** renders it and keeps the result. Every export is preserved — see
  [Files and what you sent](Files-and-what-you-sent).

Export needs a working PDF renderer. WeasyPrint is installed with Postulo and used by
default; if it cannot run, Postulo says exactly what is missing and everything else keeps
working. See [Installing Postulo](Installing-Postulo#pdf-rendering).

## A note on languages

A CV variant has a **language** field, chosen from the languages this instance offers. It
sets the language attribute of the rendered document, which matters for hyphenation and for
screen readers — including the recruiter's.

Left blank, it follows the language you read Postulo in rather than defaulting to English.
Cover letters have the same field and behave the same way.

**It also decides which text the CV prints.** Each career entry can be
[written in more than one language](Your-career-record#the-same-entry-in-another-language),
and a CV declaring French prints the French version of every entry that has one. An entry
with nothing in French prints as it stands — a line in the wrong language is better than a
gap where a job used to be — and the CV's own page lists those entries **before** you press
*Export PDF*, with a link to each. Finding out from the PDF an employer already has is not
finding out.

A cover letter is different, and deliberately: a letter is prose you wrote rather than a
selection from a record, so two languages means two letters.

It also sets the document's **direction**. A CV written in Arabic or Hebrew is laid out
right to left; one written in English is laid out left to right, whichever language you
happen to read Postulo in. The PDF goes to somebody else, so it is laid out for what is
written on it rather than for who made it.
