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
