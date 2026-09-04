# CVs

**Documents → CVs**

A CV in Postulo is a **variant**: a selection of entries from [your career
record](Your-career-record), in an order you choose, with a theme.

Make one variant per *kind* of role you apply for, not one per application. Three or four
is normal. One per application means maintaining forty documents.

## Building one

1. **New CV.** Give it a name for your own use — "Backend, English" — plus an optional
   headline and opening summary.
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

Two, for now:

- **Plain** — a clean sans-serif layout. Sober and unremarkable, which is usually right.
- **Classic** — a serif layout with small caps and a centred contact block.

Both are A4 with generous margins, and both avoid splitting a single job across a page
break.

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

A CV variant has a **language** field. It sets the language attribute of the rendered
document, which matters for hyphenation and for screen readers. It does **not** translate
anything: if you want a CV in French, write French into a French variant.
