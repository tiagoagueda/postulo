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
- **Text and contrast.** Both themes are checked for contrast at the AA level. Text
  resizes with the browser; nothing is locked to a pixel size.
- **Motion.** The little animation there is respects *prefers-reduced-motion*.

## What is checked, and how

Every page the browser test suite visits is run through **axe-core** against WCAG 2.2 at
levels A and AA on every push; a violation fails the build and names the element and the
rule. That covers what a machine can check. Using the pages with a keyboard alone, and
with NVDA and VoiceOver, is done by hand and is the part most likely to find what axe
cannot; findings become bugs with the `accessibility` label.

It is worth being plain about the limit of the automated half. The sign-in page passed
every one of those checks, in both themes, while being rendered with no styling at all:
the markup was correct, the labels were associated, black on white has ample contrast, and
the button was a button. What it did not have was any visual signal that a failed sign-in
was an error, any weight on its headings, or anything to make a control look like one. A
machine cannot see that, so a separate test now checks that Postulo's own stylesheet
reaches those pages at all, and looking at them remains the other half of the job.

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
