# Accessibility statement

**Postulo is meant to be usable by everyone, at its fullest, including people with
disabilities.** Looking for work is hard enough without the tool getting in the way, so
this is one of the project's stated commitments (`docs/PLAN.md`), not a feature. A feature
somebody cannot use is a bug, and it is filed and fixed as one.

This statement covers the Postulo application as it is published in this repository: the
pages it serves, the documents it produces and the plugins that ship inside it. An instance
somebody runs is theirs; see *For whoever runs an instance* below.

## The standard

Postulo aims at the [Web Content Accessibility Guidelines (WCAG) 2.2](https://www.w3.org/TR/WCAG22/),
level AA, in the light and the dark theme alike. WCAG 2.1 AA is the level the European
standard EN 301 549 requires of web content, so meeting 2.2 AA meets that too.

**Status: partially conformant.** Every page the browser tests visit is checked by machine
on every change, and those checks pass. No complete manual audit, with a keyboard and with
screen readers, has been published yet, and a machine finds only part of what a person
meets, so Postulo does not claim full conformance.

## What is done

- **Server-rendered HTML that works with scripts off.** Every page and every form is usable
  without JavaScript; what scripts add (a select with flags, saving as you go, inline
  editing) is drawn beside a control that already works.
- **Keyboard first.** Every control can be reached and operated from the keyboard, focus is
  always visible, menus close with Escape, and a *Skip to content* link leads every page.
- **Screen readers.** Images and icons carry a name or are marked as decorative, form errors
  are tied to their fields, and changes on the page (a filter applied, a row saved) are
  announced through live regions.
- **Colour and contrast.** Text meets 4.5:1 in both themes, colour never carries a meaning
  on its own, and the interface follows the system's *forced colours* (high-contrast) mode.
- **Motion.** The little animation there is stops when the system asks for reduced motion.
- **Right-to-left and every language.** Layout is written in logical directions, so a page
  reads correctly in Arabic or Hebrew, and every page declares its language.
- **Documents that can be read.** The CVs and letters Postulo renders are tagged PDFs
  (PDF/UA-1), so a screen reader finds their headings and reads them in order, and an
  applicant tracking system reads the words as written.

### Settings → Accessibility

Each person can change how the interface behaves for them:

- turn off the shortcuts that act on a single key (WCAG 2.1.4), or show each key beside
  its control;
- show an order number on each career entry, typed instead of moved with the arrows;
- underline the page you are on in the navigation;
- show a breadcrumb under the navigation bar, always or only while pointing at it;
- save a change as soon as it is made, or only on *Save*.

The theme, the navigation and the language are under Settings → Appearance and Settings →
Language.

## How it is checked

- **axe-core on every page.** The browser test suite injects
  [axe-core](https://github.com/dequelabs/axe-core) into each page it visits and fails on
  any violation of WCAG 2.0, 2.1 or 2.2 at level A or AA, in both themes
  (`tests/e2e/test_accessibility.py`). `tests/test_page_coverage.py` fails when a page
  exists that the walk does not visit and nobody has said in writing why.
- **Lints on the templates.** A physical direction (`left`, `right`) or a hand-built select
  fails `tests/test_template_lint.py`.
- **People.** What a machine cannot judge (whether an order makes sense, whether an
  announcement is useful) is checked by hand with a keyboard and screen readers, under the
  accessibility programme (#41), and what is found is filed as a bug with the
  `accessibility` label.

## Known limits

- **No complete manual audit yet**, as said above. Until one is published, assume a page
  can have a barrier the machine checks do not see, and please report it.
- **The companies' map** is a picture of places. Everything on it is also in the companies'
  table, which is the way to reach it without seeing the map.
- **Translations** other than British English are drafted by machine in Weblate and are
  reviewed by people over time; a draft can be clumsy or wrong. English is always one
  setting away.
- **Plugins installed separately** are not covered by this statement; each answers for its
  own pages. Plugins that ship inside Postulo are covered.
- **Files you upload** are yours: Postulo stores and serves them as they are, and cannot
  make a scanned PDF readable.

## Reporting a barrier

If something in Postulo does not work for you, or works worse than it should, please say
so. Either:

- open an issue on [Forgejo](https://source.tiagoagueda.com/postulo/postulo/issues) with the
  **Bug report** form; or
- **email the maintainer at <tiago.agueda@tiagoagueda.com>**, which needs no account
  anywhere.

It helps to say which page, what you were trying to do, and what you use (browser, screen
reader, magnifier, voice control, keyboard only) — but a report without any of that is
still welcome. You can expect an answer within a week, and a barrier is treated as a bug,
never as a request for a feature.

## For whoever runs an instance

Postulo is self-hosted, so the organisation that runs an instance is the one its users deal
with, and may have obligations of its own (in the European Union, the European
Accessibility Act or the Web Accessibility Directive, depending on who it is). This
statement can be the basis of that instance's own, but it says nothing about what an
operator adds: a theme, a separately installed plugin, a sign-in provider, the page in
front of Postulo. Those are the operator's to check.

## This statement

Written on 2026-10-18 (#1008). It is updated when something it says changes: a new
setting, a limit lifted, an audit published.
