# Accessibility

Postulo is meant to be usable by everyone, at its fullest, including people with
disabilities. That is one of the project's stated commitments, not a feature; this page
says what it means in practice, what is checked, what is known not to be, and how to tell
us about a barrier.

## What you can expect

- **Everything works from the keyboard.** Every control can be reached in a sensible
  order and operated with Enter, Space and the arrow keys; a *Skip to content* link is the
  first thing Tab reaches; menus open and close with Enter and Escape; focus is always
  visible.
- **Everything works without scripts.** The pages are server-rendered HTML. Scripts add
  convenience — the table narrowing as you type, the password meter, the theme applying
  before the server confirms it — and every one of them has a plain button that does the
  same thing.
- **Each language names itself, and says so.** The language menu lists every language
  under its own name, and each entry is marked with the language it is in, so a screen
  reader pronounces it properly rather than reading Greek with English rules. How well
  translated each one is appears as a heading over the group rather than inside the entry.
- **Screen readers are told what changes.** Counts that update, the password meter's word,
  a search result summary and the messages after an action are live regions. Icons that
  stand alone have names; icons beside words are marked decorative. Tables have headers,
  sortable ones say which way they are sorted.
- **Colour never carries a meaning on its own.** A status has its label; the strength
  meter has its word; a quiet application says so in text.
- **The page follows the direction of the language it is in.** In a right-to-left
  language the whole interface flips — headings, action buttons, the board's columns, the
  timeline's rule, the menus, the skip link. See *Right to left* below.
- **Text and contrast.** Both themes are checked for contrast at the AA level. Text
  resizes with the browser; nothing is locked to a pixel size.
- **Motion.** The little animation there is respects *prefers-reduced-motion*.
- **Things you click are big enough to hit.** Every button, link and switch is at least
  24 by 24 pixels, or else has that much clear space around it. This matters most on a
  phone, with a tremor, or with any pointer that is not a mouse on a desk.
- **Nothing scrolls sideways, in any language.** At 320 pixels — the width a normal window
  has at 400% zoom — every page reads in one column, top to bottom, in Greek and German as
  well as in English, and no sentence is squeezed into a strip beside a button. Only a data
  table scrolls across, in its own box, because a table needs its two dimensions to mean
  anything.
- **A rejected form says why, to a screen reader too.** When a field is refused, the
  control is marked invalid *and* points at the message explaining it, so the reason is read
  out with the field rather than sitting in red where only eyes can find it.

## What is checked, and how

Every page the browser test suite visits is run through **axe-core** against WCAG 2.2 at
levels A and AA on every push; a violation fails the build and names the element and the
rule. That covers what a machine can check. Using the pages with a keyboard alone, and
with NVDA and VoiceOver, is done by hand and is the part most likely to find what axe
cannot; findings become bugs with the `accessibility` label.

Every page a person can reach is on that list, and that is now enforced rather than
remembered: a separate test walks the application's own URL table and fails unless each one
is either visited by the browser suite or named with a reason it is not a page — a file
download, a form submission, a redirect. Adding a page without deciding about it breaks the
build.

Size is measured rather than read. axe-core does not enforce *Target Size (Minimum)* —
it reports the rule as needing review rather than as a violation — so the suite above was
returning a clean result on pages carrying sixty-two buttons that were 22 pixels square.
A second browser test now asks every clickable thing on every one of those pages for its
box and holds it to 24 by 24, allowing the criterion's own exceptions: a small target with
24 pixels of clear space around it, a checkbox measured by the label that switches it, and
a link inside a sentence, whose height belongs to the prose it sits in. What it cannot
judge is whether some other control does the same job at full size; that needs a person,
and anything relying on it has to be written down.

References are resolved, which is a third thing a machine can check and axe does not. Every
`aria-describedby` on every page is looked up against the document, and any that names an
element which is not there fails the build. That was the state of every form Postulo drew
itself: Django marks a refused field `aria-invalid` and points it at its error, correctly,
and the template drew that error without the id the field was pointing at. The control
announced that it was invalid and that two elements described it, and neither existed. The
forms are also submitted empty on purpose, because a page that has never been refused has no
errors to point at and the half of the bug that matters would never be reached.

Width is measured too, and by the same method: at 320 CSS pixels a third browser test
asks each page to scroll sideways and fails if it moves. That is the criterion's own
question — *Reflow*, level AA — and it is not one axe can answer, because it is about
layout at a width rather than about a document. Nothing had ever looked at these pages
narrow, and every one of them scrolled, by an identical 331 pixels: a single row of
navigation links that did not care how wide the window was. The failure names the element
rather than the page, because that is the difference between a fix and a search.

The walk is taken in three languages: English, Greek and German. A page is made of
translated words, and a word that cannot break is as wide as its language makes it. While
the walk was English only, the dashboard's arrange page fitted with seven pixels to spare on
one machine and was eight over on another, and in Greek it was 64 over on every machine.
Greek and German are there because they drew widest of the European catalogues where they
were measured.

It also asks a second question, of every box that holds words: do they fit inside it? A row of
words beside buttons that will not give way does not scroll when it fails — it hands the words
whatever width is left over. On the arrange page that was a column one word wide, with the
longest written across the arrows; each built-in plugin's description got six pixels. The
page was exactly as wide as the screen, and half of it could not be read.

It is worth being plain about the limit of the automated half. The sign-in page passed
every one of those checks, in both themes, while being rendered with no styling at all:
the markup was correct, the labels were associated, black on white has ample contrast, and
the button was a button. What it did not have was any visual signal that a failed sign-in
was an error, any weight on its headings, or anything to make a control look like one. A
machine cannot see that, so a separate test now checks that Postulo's own stylesheet
reaches those pages at all, and looking at them remains the other half of the job.

## Right to left

Arabic, Hebrew, Persian and Urdu are read right to left, and a page in one of them has to
be laid out that way — not merely have its text reversed inside a left-to-right shell.

**What flips.** Everything that names an edge: the action buttons at the end of a heading
row, the timeline rule beside the event log, the menus that hang from a corner, the skip
link, table alignment, and the board's columns, so the earliest status is the one nearest
where you start reading. Icons that point sideways are mirrored; ones that point up or
down are not, because they mean the same thing either way.

**What does not flip.** Text that is not in your language. A company called *Aperture
Science* stays Latin and stays left-to-right inside an Arabic line, and so do email
addresses, web addresses, version numbers, package names and checksums. They are isolated
so the punctuation around them stays where it belongs — without that, a separator jumps to
the wrong end of the line and the row reads as nonsense.

**A document has its own direction.** A CV or a cover letter is laid out for the language
*it* is written in, not for the language you read Postulo in. Somebody using Postulo in
Arabic who writes an English CV gets an English, left-to-right PDF; the reverse holds too.
That is what the *language* field on a CV and a letter decides, along with hyphenation and
how a screen reader pronounces it.

**How it is checked.** The browser suite visits the application in a right-to-left
language, in both themes, and runs axe over it; it also measures that the action buttons,
the board and the skip link actually moved rather than merely being labelled as though
they had. A separate lint refuses any stylesheet class that names a left or a right, which
is what stops this drifting back one heading row at a time.

**No right-to-left language is offered yet.** The layout work is done and tested first, so
that the language can be added as a catalogue and nothing else.

## Known gaps

- The PDFs Postulo renders are not yet tagged PDFs, so a CV a blind person makes here is
  not yet one a blind recruiter can read with a screen reader. It is on the list.
- Colour contrast in the *brand* accent on dark backgrounds is checked by tooling only;
  if it reads badly to you, say so.

## Telling us

A feature somebody cannot use is a bug. Open an issue on the repository with the page,
what you were trying to do, and the assistive technology you use, or write to the
maintainer if you would rather not open a public issue. It will be treated as a bug, not a
request.
