# Contributing to Postulo

Thank you for considering it. Postulo is developed on
[Forgejo](https://source.tiagoagueda.com/postulo/postulo); the GitHub repository is a
read-only mirror, so please open issues and pull requests on Forgejo.

## Opening an issue

*New issue* on Forgejo offers two forms, **Bug report** and **Feature request**, and each
asks for what a fix or a decision needs, so use them rather than a blank box (the
maintainer's long hand-written issues are the exception, and blank issues stay allowed). A
vulnerability is not a public issue: follow [SECURITY.md](SECURITY.md). Remove personal
details from any log or screenshot first. The forms live in `.forgejo/ISSUE_TEMPLATE/`.

## Getting set up

Requires Python 3.12.4 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pre-commit install
cp .env.example .env
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver
```

## Before you open a pull request

```sh
uv run ruff format .
uv run ruff check --fix .
uv run pytest -n auto        # every core; plain `uv run pytest` is the same suite, slower
                             # (CI runs `-n 2`: its jobs share one host, see ci.yml)
uv run manage.py makemigrations --check --dry-run
uv run pytest -m step         # the catalogue and static-file checks CI's Checks job runs
npm run build:css            # only if you touched assets/css/ or a template's classes
```

Continuous integration runs the suite on Python 3.14 for every push, and on 3.12 and 3.13
in its *Every Python* workflow: weekly, on release branches, and whenever it is started by
hand. The rest of the above runs once, in its *Checks* job, together with the catalogue checks,
`manage.py check --deploy` against production settings, and a check that the committed
stylesheet has not drifted from its source. Coverage is measured on the 3.14 leg only, and
`fail_under` in `pyproject.toml` is the floor it has to clear; run
`uv run pytest -n auto --cov` to see the number yourself. So does `uv run pytest` on your machine, once
`npm ci` has installed the Tailwind CLI: `tests/test_stylesheet.py` rebuilds it and compares,
which is the difference between finding out before the push and after it.

There is also a browser suite, `tests/e2e`: some 650 tests in a real Chromium against a live
server, with axe-core and a walk of every page. It is left out of the default run because it
needs a browser:

```sh
uv sync --group e2e
uv run playwright install chromium
uv run pytest -m e2e -n 4        # about eight minutes on four workers; CI runs it on one
```

CI runs it on every push, and a failure is run again alone with a trace. Run it before a pull
request that touches a template, a stylesheet or a script.

**What belongs in it, and how to keep it fast.** It grew from 149 tests to 645 in five days of
October 2026, and every one costs seconds where a unit test costs milliseconds (#726).

- A browser test is for what needs a browser: scripts, layout, focus, the content security
  policy. Markup, permissions, redirects and what a form saves belong in the unit suite,
  with Django's test client.
- A new page joins the walk (`signed_in_paths()` in `tests/e2e/test_accessibility.py`), and
  a check every page should pass joins the walk too, rather than walking the site again.
- Sign in with `tests/e2e/signing_in.py`; fill the sign-in form only in a test about it.
- Wait for state with `expect(...)`. A fixed `wait_for_timeout` is for a test whose subject
  is time, and carries a comment beginning `# A fixed wait on purpose:` saying what it
  measures.
- CI's log ends with the 25 slowest tests. Read it before adding to the suite.

**Every page is drawn in the suite's own font.** The interface uses the reader's own system
font, so the browser suite used to measure whatever the machine it ran on drew in -- Segoe UI
on a desktop, DejaVu Sans on CI -- and a row that fitted on one spilled on the other. The
suite now brings DejaVu Sans with it (`tests/e2e/fonts/`) and draws every page in it, on
every machine, so a layout that passes on your desktop passes on CI. Nothing needs
installing or remembering; the run stops at once, saying so, if the font does not load.

## The mark

Postulo's mark is a layered paper-cut **P**. It lives once, at `assets/brand/postulo.png`,
and everything served is derived from it:

```sh
uv run python scripts/brand.py          # write the derived images
uv run python scripts/brand.py --check  # CI runs this
```

That produces the favicons, the Apple touch icon, the two manifest icons and the header
logo into `src/postulo/static/brand/`, all committed — the same arrangement as the compiled
stylesheet and the icon set, so an instance runs without needing the tooling that made them.
Change the source, run the script, commit both; CI fails if they disagree.

The mark is **decorative everywhere it appears**. It always sits beside the instance's name,
so it carries an empty `alt` and a screen reader is not made to say "Postulo logo, Postulo".

The file in this repository is 1024 pixels square, which is twice the largest thing derived
from it. The full-resolution artwork lives wherever it is being designed; a source tree is
not an asset library.

It is a work in progress and will change. Nothing should hard-code its colours: the
stylesheet's `brand` palette is the source of truth for those.

## Icons

The interface uses [Lucide](https://lucide.dev) icons, inlined by the `{% icon %}` tag:

```django
{% icon "sun" class="size-5" %}                {# decorative, beside a word #}
{% icon "x" label="Close" class="size-4" %}    {# standing alone, so it needs a name #}
```

Only the icons listed in `assets/icons.txt` are in the repository. To use a new one, add
its name to that list, run `npm run sync:icons`, and commit the copied file with your
change; CI fails if the two disagree. Icons decorate — a button is a word with an icon
beside it, not an icon alone — except where the header is too narrow for words, and there
the icon gets a `label`.

## Flags

Country flags are [flag-icons](https://github.com/lipis/flag-icons) (MIT), drawn by the
`{% flag %}` tag from an ISO 3166-1 alpha-2 code:

```django
{% flag "pt" %}                        {# decorative, beside a name #}
{% flag country label="Portugal" %}    {# standing alone, so it needs a name #}
```

An **image**, not the flag emoji. A flag emoji is not a character: it is two *regional
indicator* code points — U+1F1F5 U+1F1F9 for Portugal — and a font is invited rather than
required to draw the pair as a flag. Segoe UI Emoji never has, so every Windows machine
drew `PT` (#88). This paragraph deliberately does not print the emoji to make its point,
for the same reason. Treat any emoji whose meaning depends on a font having agreed to it
the same way.

The tag renders **nothing** for a code it does not have a flag for, and every caller has to
cope with that: a language with no uncontested home is left blank rather than given
somebody's best guess. No flag beats a wrong flag.

`assets/flags.txt` lists what is in the repository, `npm run sync:flags` copies exactly
those, and `tests/test_flags.py` fails if the list and `core/phones.py` disagree — so a
country added to the telephone field is a missing flag until the script is run.

An `<option>` element can hold text and nothing else, so a native `<select>` of countries
cannot show flags in its list. The control built beside every select does (see *Selects*,
next): give each option its own flag's URL in `data-flag`. Where the chosen flag should
show with scripts off as well, draw it beside the select as `partials/phone_widget.html`
does, in a holder marked `data-flag-holder`; the script puts the holder away, so the flag
is never drawn twice.

## Selects

**Every `<select>` is Basecoat's select** (#301): a button showing the choice and a list
that opens from it, whose options are elements and so can hold a flag or an icon. You
write a native `<select>`, or let a form field draw one, and that is all: `app.js` builds
the button and the list beside it. The native select stays in the page and stays the
form's control -- it is the whole control with scripts off, it posts the name and the
value, and a choice made in the list sets it and raises its `input` and `change`. So htmx
on a select, a script listening for `change`, and a stylesheet rule that follows a select
with `:has(select option[value="other"]:checked)` all work as they did. A script that sets
a select itself calls `selectChanged(select)` afterwards, because setting a value raises no
event.

Never write Basecoat's own markup for one (`<div class="select">` round a button): it does
nothing until a script has run, and `tests/test_template_lint.py` refuses it.

- **To keep one select native**, write `data-native` on it and add it to
  `NATIVE_ON_PURPOSE` in `tests/test_template_lint.py`, with the reason. The list is empty,
  and the lint fails on a `data-native` it does not list and on an entry no template bears
  out. A `<select multiple>` is a list box, not this control, and is left alone.
- **A flag beside a language or a country**: `LanguageSelect` and `CountrySelect` put the
  flag's URL on each option, in `data-flag`. A language with no single home carries an
  empty one and draws nothing.
- **An icon beside a kind**: the option names a Lucide icon Postulo ships in `data-icon`,
  generic and never somebody's mark (`TRADEMARKS.md`). `IconSelect(icons={value: name})`
  does it for a form field, and draws the icons the list can show into a
  `<template data-option-icons>` after the select, where the script copies them from: a
  script builds no SVG from a string. A select written out in a template says `data-icon`
  on its options and draws the template once for the page with
  `{% option_icons names %}`, as the board does for all its cards.
- **A list of sixteen options or more** gets a box that narrows it, whatever the case or
  the accents. Nothing to do.
- **A long list in groups** is `<optgroup>`, which Django draws from nested choices; the
  list shows the same groups.

In a browser test, a select is still a select: `select.select_option("work")` and
`expect(select).to_have_value("work")` on the native control say *this field holds that
value*, and the button follows. What a person sees and does goes through
`tests/e2e/selects.py`: `button_of(select)` for the button (its box is where the select
was; the select itself is one pixel), `open_list`, `options_of`, `choose`. A label names
both the select and its button, so `get_by_label("Type")` alone finds two things:
ask for `get_by_role("combobox", name="Type")`, which is the button, or narrow the label
to the select with `.and_(page.locator("select"))`.

## Tables

A table wider than the card it sits in scrolls sideways, and a card under *Settings*, or on
a form under *Server settings*, stops at 672 pixels (`max-w-2xl`) at any desktop width
(#320) — so a table that does not fit scrolls on a desktop, not only on a phone. A page
under *Server settings* that is a table leaves the `measure` block empty and takes the
width, as *People* does. Measure before assuming otherwise:
`card.scrollWidth - card.clientWidth` in the browser, at 1440 as well as at 390.

Two things keep one honest:

- **Row actions are a menu**, not a row of buttons. Four buttons with their labels spelled
  out made one column of *Server settings → People* 335 pixels wide — larger than the email
  column, and holding no information at all. Use `<c-dropdown-menu>`, the component every
  menu is drawn with: a popover, which the browser opens and closes with no script and
  draws in the top layer, where the box the table scrolls in cannot cut off the last row's
  menu (#310).
- **`table-cards`** turns each row into a card below the `md` breakpoint, where columns have
  nowhere to go. Give every cell a `data-label` naming its column, except the one that is
  the card's heading.

`table-cards` works by making table elements blocks, and **that strips the table semantics a
screen reader navigates by** — "Administrator" stops being the Role of a row and becomes a
loose word. So a table using it must carry `role="table"`, `role="rowgroup"`, `role="row"`,
`role="columnheader"` and `role="cell"` explicitly. On a wide screen those roles are what
the elements already are and change nothing; on a narrow one they are the only thing holding
the meaning together. `server/people.html` is the worked example.

## Help

Help goes where the question is asked (#302), and there are three places for it. *Your
details* is the worked example. A page takes this shape when somebody gives it to it; until
then its help stays under its fields, as it was.

- **What a field is *for* is a tooltip.** `<c-field :field="form.headline" :tip="tips" />`,
  or `tip` on `<c-field-feedback>` in a row written by hand. It is the field's `help_text`,
  drawn as the same paragraph with the same id, still named in the control's
  `aria-describedby`, so a screen reader reads it with the field exactly as before. `app.js`
  shows it while the field is hovered or has the focus, and Escape puts it away. With
  scripts off it is under the field.
- **A format or an instruction stays in sight**, with no `tip`: the form a date takes, the
  files an upload accepts, which addresses can be pasted whole, what to type in a box that
  has nothing to go on yet, the row a new telephone number is typed into. It has to be
  there before the mistake (WCAG 3.3.2), and a tooltip is out of sight for everybody who
  does not think to look for one. The same goes for the sentence of a tick box, a radio or
  a switch, which says what ticking does: a tap does not give one the focus in every
  browser, so a phone would show it after the tick or never, and `<c-field>` takes no `tip`
  on one. A help text that says both is split: the format stays the field's help and the
  rest goes to the card's.
- **Help that belongs to a card, and not to one of its fields, is behind a question mark.**
  `<c-section-title help="telephone-numbers" anchor="section-phones">`, or in a card that
  is a `<fieldset>`, `<c-help-mark topic="…" anchor="…" />` after its `<legend>`. Resting
  on the question mark, or focusing it, shows one sentence; pressing it, or Space on it,
  opens the card's help in a drawer. With scripts off the sentence is under the card's
  title and the question mark is a link that opens `/help/<topic>/` **in a new tab**, and
  says so in its name: the card is a form, and leaving it would lose what was typed. That
  page holds the same help and leads back to the card. **A card with nothing more to say
  than its one sentence gets no question mark**: a control that opens what the tooltip
  already said does nothing. Give that card a `subtitle` and leave it there.

**A page that has been refused has no tooltips.** The view says so for the whole page --
`refused`, true when the form or any block of rows on it is invalid, and `tips`, its
opposite, which is what the fields take -- and every question mark's sentence is then under
its card's title (`:in_sight="refused"`). A refusal on one row is under the next row's
help, and a card's sentence is the tooltip of fields far from the refusal, so asking each
form about itself is not enough. `<c-field-feedback>` still keeps the help of a form that
has errors in sight, whatever it is told.

**A tooltip is shown only where there is a clear place for it**, and `app.js` decides that
by measuring: wholly in the window that can be seen (under the floating masthead, above a
phone's bar at the foot), and not over its own field, a refusal or an alert, a mark or a
note under a row, a card's question mark, or the control that has the focus. Where there is
none it goes back to being the paragraph under its field. So a page needs nothing more than
the `tip`; but a new kind of thing that must never be covered -- a new mark under a row --
goes into `TIP_KEEPS_CLEAR_OF` in `app.js`, and into the rule the browser tests ask
(`BY_THE_RULE` in `tests/e2e/test_help.py`).

Never use Basecoat's `data-tooltip="…"` attribute: it is a `::before`, shown on hover only
and hidden on focus, and a screen reader does not reliably read it.

**To add a help topic:**

1. Write it as a template under `templates/help/`: a few short paragraphs, each one
   `{% blocktranslate trimmed %}`, for the person filling the card in. Say what the card is
   for, what each control does, what is refused and why, and what happens to what is there
   already. No heading: the drawer and the page supply it. **Every sentence has to be
   true**, so read the code that makes it so, or try it: a sentence that was nearly right
   is how *Your details* came to promise that a form printed your street.
2. Add a `Topic` to `core/help.py` with its address, the card's name, the one sentence and
   the template. **A number or a list the code decides is read from the code**, through
   the topic's `context`, and never written into the text: the help of *Your picture* gets
   its file types and its limits from `accounts/avatars.py`, where an upload is refused by
   them, and the help of *Identifiers* gets the registers whose address can be pasted from
   the registry. No counts in the prose ("three cards"), no defaults ("off to begin with"),
   no list of kinds that a plugin can add to.
3. Put the question mark on the card, and translate the new strings as drafts.
4. Where a wiki page says the same thing, give it a line pointing at the topic: the topic
   is what a person filling the card in reads, in their own language, and the wiki is
   where it is explained at length.

The text is never fetched from the wiki and never bundled from it. The drawer and the page
draw the same template through one tag, and `tests/test_help_topics.py` holds them to it.

## What will not be merged

**Anything that puts a feature behind payment.** Postulo will never have a paid tier, a
"pro" edition, a licence key, or a feature that unlocks later — people looking for work
usually cannot pay for the tools to find it, and this project exists for them. A pull
request that introduces any of those will be declined however good the code is. The same
goes for anything that nudges towards paying: upgrade prompts, feature comparison
tables, "premium" labels.

**Anything that weakens a security boundary.** Postulo holds people's CVs and employment
histories, and keeping them secure is one of its stated missions. A change that serves a
file without the ownership check, loosens the content security policy, adds an inline
script, makes an outbound request nobody asked for, or trusts a value from the query
string will be declined until it does not. New surfaces come with security tests.

**Anything somebody cannot use.** Being usable by everyone, including people with
disabilities, is the other stated mission. A control that cannot be reached from the
keyboard, an icon with no name, a meaning carried by colour alone, a change on the page
that a screen reader is not told about, or a page that breaks with scripts off is a bug,
and a pull request that introduces one will be asked to fix it first.

## Code written with AI

Code written with the help of an AI assistant is welcome on the same terms as any other
code: a person proposes it, has read it, can explain it, and answers for it. The
assistant is a tool the contributor used, not a contributor; a pull request is a
conversation between people, and the person opening it is the one who will be asked
what a line does and why. Say that a model helped, the way you would credit a library.
Review what it wrote with the care you would give a stranger's patch, especially around
ownership scoping, file handling and anything the security tests cover, because a
plausible-looking mistake is the kind that gets through. `CLAUDE.md` in the repository
root tells an assistant how this project works; keep it current when the rules change.

## House style

- **British English** in code, comments, documentation, and interface text: *organise*,
  *colour*, *licence* (noun), *behaviour*.
- **Wrap every user-facing string** in `gettext` / `gettext_lazy`, or `{% translate %}`
  in templates. Untranslatable strings are treated as bugs. Wrap the *whole sentence*:
  a verb translated on its own and a date printed beside it is half a clause, and no
  language promises to keep the two in that order. Put the link inside the
  `{% blocktranslate %}` with its address as a variable, and pluralise a unit with
  `{% blocktranslate count %}` rather than writing "days" after a number field.
- **Never write a date format out.** `|date:"j M Y"` fixes day before month before year and
  the hour at 14 rather than 2 p.m. for every language at once. Ask for one of Django's
  names — `DATE_FORMAT`, `DATETIME_FORMAT`, `SHORT_DATE_FORMAT`, `SHORT_DATETIME_FORMAT`,
  `TIME_FORMAT`, `YEAR_MONTH_FORMAT`, `MONTH_DAY_FORMAT` — in the filter or in
  `django.utils.formats.date_format`, and compose two of them where you need a weekday as
  well. Do not invent a name: `get_format` returns an unknown name to the page as it is, so
  a name one locale has not defined is printed to whoever reads in that language.
  `tests/test_template_lint.py` fails on a format written out in a template or a view.
- **Never name a side of the page.** Use the logical utilities — `ms`/`me`, `ps`/`pe`,
  `start`/`end`, `text-start`/`text-end`, `border-s`/`border-e` — not `ml`, `pl`,
  `text-left` or `left-0`. They mean the same thing under `ltr` and the right thing under
  `rtl`; `tests/test_template_lint.py` fails on a physical one. Wrap text a person typed
  in `<bdi>` where it sits inline beside other text, so a Latin name inside an Arabic line
  does not throw the punctuation to the wrong end.
- **What the interface should look like is written down** (#292): the wiki's
  [Design](https://source.tiagoagueda.com/postulo/postulo/wiki/Design) page says what the
  tokens are and when to reach for each, and *Server settings -> Design* in a running
  instance shows every component on one page in both themes. Read the gallery before
  adding a component; a change to the paint should be visible somewhere before it is
  spread across 184 templates. The short version: one `--radius` and the roundings derived
  from it, three planes rather than a blur radius, colour never the only thing saying
  something, and no motion that was not opted into behind `prefers-reduced-motion`.
- **Markup used on more than one page is a component**, in `templates/cotton/`, written
  as `<c-field :field="form.name" />` and declaring what it takes in a `<c-vars>` line at
  the top of the file (django-cotton, #263). `{% include %}` is for a fragment of one
  page. A component can see the caller's whole context; read only what the tag was given,
  so the line at the top stays the truth about it. Isolation is off on purpose: switched
  on, it runs every context processor again for each component on the page.
- **Components look the way [Basecoat](https://basecoatui.com/) draws them** (#262): a
  button is `class="btn" data-variant="outline" data-size="sm"`, with the variants and
  sizes `assets/css/basecoat.css` paints. That file is Postulo's style pack -- Basecoat's
  structural files are imported one at a time in `assets/css/app.css` and painted there in
  Postulo's palette; the bundle, the base tokens and the eight style packs are not
  imported, because each would repaint this project's cards and alerts and change the
  `dark` variant. `tests/test_stylesheet.py` refuses an import whose classes `app.css`
  already defines, so a collision is a decision made in the source and never left to the
  cascade. A Basecoat component that needs its script still has to work with scripts off
  before it is adopted; the ones that cannot are written down in #262.
- **Every user-owned model** inherits the shared owned-model base and is filtered by
  owner in every query. Cross-account data leaks are the one bug class we test for
  explicitly, so new models need a test proving isolation.
- **Personal documents are private.** Never serve uploaded media directly; deliver them
  through `postulo.core.files.serve_private_file` from a view that has already
  established who is asking.
- **No `unsafe-eval`.** The Content-Security-Policy is strict on purpose. Client-side
  behaviour is htmx plus plain JavaScript, which is why Alpine.js is not used.
- **Prefer an interface with a built-in plugin over a hard-coded implementation** whenever
  a second implementation is plausible. Capture sources work this way; notifications
  will. If you find yourself writing `if backend == "x"`, that is usually a plugin
  boundary asking to exist.
- **Security tests are part of the feature.** A new endpoint gets a test that another
  account cannot reach it, that a forged request is refused, and that whatever it reads
  from the request is validated. Run `uv run pytest tests/test_ownership.py` and friends
  before you open the pull request; CI audits the dependencies too.
- **Accessible by construction.** Every input has a `<label>`; icons that stand alone get a
  `label`, the rest are decorative; anything that changes without a page load sits in a
  live region or is announced; `<details>` and real links before scripted widgets; test
  the page with the keyboard alone before calling it done. The browser suite runs axe-core
  over every page it visits (`uv run pytest -m e2e tests/e2e/test_accessibility.py`); add
  a new page to its list.
- Keep commits focused, and describe *why* in the message rather than *what*.

## Look for a library before you write one

Before you write code that implements a standard, a format, a data set or a well-known
algorithm, look for an open-source library that already does it, and say in the pull
request what you found. It can save work and time, and a hand-written parser is where
Postulo has had its injection bugs (#218, #450). Take a library when:

- its licence is compatible with AGPL-3.0-or-later: MIT, BSD-2-Clause, BSD-3-Clause, ISC,
  0BSD, Apache-2.0, MPL-2.0, LGPL-3.0, CC0, AGPL-3.0 and GPL-3.0 are taken; GPL-2.0-only,
  BSD-4-Clause with its advertising clause, non-commercial and source-available terms are
  not;
- it is maintained;
- `pip-audit` or `npm audit` (or OSV) report no unpatched advisory in the range the lock
  would hold;
- its cost is stated in the pull request: its size, a compiled or system dependency, and
  what it brings with it. Weight is a cost, not a veto: a compiled extension for every
  instance weighs heavily, and a pure-Python library with no new dependency does not;
- it fits the content security policy and makes no request nobody asked for.

A library with an **unpatched severe advisory** (High or Critical, CVSS 7.0 and up, and no
fixed release) is not taken, and writing it by hand is then right; the pull request says
which advisory. An advisory with a fix is met by raising the floor in `pyproject.toml`; one
whose fix cannot be taken yet is ignored by name, with a test that expires the ignore
(`tests/test_security_audit.py`), and the library is still taken.

The rule applies to new code and to code a commit touches; working code is not rewritten
for it. It binds plugins too. A package from the index gets a comment in `pyproject.toml`
saying why it is there; files copied into the tree get a line in
[THIRD-PARTY.md](THIRD-PARTY.md).

## Formats

What a new export or import picks, in this order, and a format further down the list needs
a reason in the issue:

1. **JSON** for data of every kind.
2. **vCard 4.0** for contact details.
3. **iCalendar** for events and anything with a date and a time.
4. **PDF** for a document, then **ODT**, then **DOCX**.

CSV stays where a spreadsheet is the other end of the exchange, as in the report and the
spreadsheet import.

## Where a picture or a file lives

A file a person uploads or Postulo keeps for them is a file under private media, deleted with
its row by a receiver and served through an ownership-checked view: documents, renders,
kept pages and exports. **Two pictures are rows instead** (#662): a profile's avatar and its
Gravatar copy (`ProfilePicture`) and a company's logo (`CompanyLogo`). Each is at most a
mebibyte, each belongs to exactly one owner, and a foreign key with `on_delete=CASCADE`
deletes it with that owner in the same statement, however the owner goes, with nothing to
remember. A new picture of that kind subclasses `core.stored_pictures.StoredPicture`, is
written only through `core.pictures.keep` (after the bytes have been through `as_stored` or
`sanitise_svg`), and its owner says on its own row whether one exists, so reading the owner
never reads the bytes. A new kind of file that can be large stays a file.

## Documentation

User documentation is the [wiki](https://source.tiagoagueda.com/postulo/postulo/wiki), and
the wiki is its own git repository. Clone it beside your checkout of this one:

```sh
git clone https://source.tiagoagueda.com/postulo/postulo.wiki.git
```

Pages are written and committed there, and nowhere else: there is no copy in this repository
to keep in step (#169). A change that needs a page ships with one — a commit to the wiki that
names the same issue as the code, pushed when the code is.

- **A file name is a page name.** `Installing-Postulo.md` is the page *Installing Postulo*.
  `Home.md` is the front page, and `_Sidebar.md` is the navigation beside every page, so a
  new page gets a line there.
- **Links between pages** use the page name without the extension:
  `[Configuration](Configuration)`.
- **Images** go in `images/` and are referred to by a relative path, `images/postulo.png`;
  Forgejo serves them from the wiki repository.
- **A page read before there is an instance** (*Home*, *Installing Postulo*, *Getting
  started*) has translations under `lang/<tag>/` and is built into a static site by
  `scripts/wiki_site.py`; when you change the English of one, run `wiki_site.py stale` and
  say in the commit which translations now lag (*Translating*, `docs/TRANSLATING.md`, #352).
- **Pictures of the interface are generated, never taken by hand** (#353). A page that
  describes a screen shows it with `![what it shows](images/board.png)`, and the alternative
  text says what the picture shows, because the reader may not see it. Add the page to
  `SHOTS` in `scripts/screenshots.py` (a name is a file name and is permanent, so the Markdown
  never changes when a picture is retaken), then run `uv run python scripts/screenshots.py`:
  it seeds a throwaway database with `seed_demo --seed 2026`, freezes the clock, draws every
  page in the suite's own font and writes the PNGs to the wiki's `images/` and, for the few the
  README shows, to `assets/screenshots/`. `--only NAME`, `--language fr`, `--list` (the names
  and the Markdown to paste) and `--check` (say which pictures are stale, write nothing) are
  its other modes. It needs `uv run playwright install chromium`. A picture shows only the
  demo person and the demo employers, which are fictional; never add a page to `SHOTS` that
  would show something from the machine taking it, and never a real company's logo
  (`TRADEMARKS.md`). Pictures are retaken before a release (below) and committed in the wiki
  with the release's wiki changes.

Please keep it honest: the wiki says plainly what is not built yet, and a page that
describes a feature which does not exist is a bug.

Three pages are held to the code by `tests/test_wiki_surface.py` whenever the wiki is
checked out beside this repository: *The API* lists every call with its scope, *Health,
metrics and logs* every endpoint for machines and every metric, and *Writing a plugin*
every name `postulo.plugins.api` promises and every kind of plugin. A new call, metric or
name fails the suite until its line is written.

## Translations

Postulo is written in British English and translated from there. French and Portuguese
catalogues exist and are waiting for contributors — see
[docs/TRANSLATING.md](docs/TRANSLATING.md).

## Keeping dependencies current

The `Dependencies` workflow runs `uv lock --upgrade` once a month (and on demand from
*Actions*), runs ruff and the suite on one Python against the result, and opens a pull
request only if that passed, moving the pre-commit ruff `rev` to the locked version. The
pull request is pushed with the `DEPENDENCIES_TOKEN` secret (a bot account), so `ci.yml`
runs on it as on any other; merge when that is green. What is left for a person is the reading: the changelogs of anything that moved a major
version. Merge the lock file on its own. Between runs, when the `security` job says so,
the same by hand: `uv lock --upgrade`, run the suite, commit the lock file on its own.

The tools CI runs are pinned as the lock is -- uv by version in every workflow, in
`.pre-commit-config.yaml` and in `docker/Dockerfile`, and zizmor and pip-audit in `ci.yml`
-- so that a push that changed nothing cannot fail on a tool that did. Bump them together,
deliberately. `docs/THREAT-MODEL.md` says who the attackers are and which rules follow; a pull
request that touches a boundary answers to it.

## Writing a changelog entry

**An entry is one line: what changed, who it affects, and the issue it closes.** The
reasoning -- why, what was rejected, what broke -- belongs in the issue and in the commit
message, where it is already written and where the code sits beside it. The changelog
section becomes the release's notes verbatim, and 0.3.0's ran to forty-seven thousand
words because every entry carried its reasoning too (#254); a release note somebody
cannot scan in two minutes is not one. Keep an entry under about three hundred
characters; `tests/test_changelog.py` holds *Unreleased* to that. A security entry may add
the one sentence an operator needs to decide whether to hurry, and a breaking change the
line that says what to do about it, in bold.

Each goes under *Unreleased*, in one of six sections, each marked so the file can be
scanned rather than read:

| | For |
| --- | --- |
| `### ✨ Added` | a capability that was not there |
| `### 🔧 Changed` | behaviour that existed and is now different |
| `### 🐛 Fixed` | a defect |
| `### 🔒 Security` | anything with a security consequence |
| `### ⚠️ Deprecated` | on its way out |
| `### 🗑️ Removed` | gone |

The word stays beside the mark: the word is what reads in a terminal, to a screen reader,
and in a font that has no glyph for the mark. `tests/test_changelog.py` refuses a heading
that is not one of these, or one carrying the wrong mark.

End the entry with the issue it closes, in brackets: `(#42)`.

## Making a release

1. `uv run pytest -m release` — the promises that must hold in a published version rather
   than in every commit. Today that is the twenty-four European Union catalogues being
   complete. Ordinary work translates English, French and European Portuguese; **this is
   where the rest are swept up**, and it is the only check between an unfinished catalogue
   and a published version. Fill what it names (`scripts/messages.py extract` first if the
   strings are new) and run it again until it passes.
1b. `uv run python scripts/screenshots.py --check` names the documentation's pictures that
   no longer match the interface. Run `uv run python scripts/screenshots.py` to retake them,
   look at what changed, and commit the new files: those in `assets/screenshots/` here, those
   in `images/` in the wiki (#353).
2. Move the *Unreleased* entries in `CHANGELOG.md` under a new `## [X.Y.Z] — YYYY-MM-DD`
   heading, and leave an empty *Unreleased* above it.
3. Set the same version in `pyproject.toml` and in `src/postulo/__init__.py`.
   Re-read `docs/PLAN.md` while there -- the repository layout and the open assumptions
   in particular -- and revise what the release made untrue. The plan explains why the
   code is shaped as it is and says nothing about where the project stands, so this is
   the one moment it is revised (#255).
3b. Move the image tag in `docker/compose.yml` and `docker/compose.postgres.yml` (every
   `postulo/postulo:X.Y`) to the new minor, or an install runs the last release (#403).
4. `python scripts/release_tools.py check vX.Y.Z` says whether the three agree, and that
   both compose files name this minor.
5. Commit and push. A push tests the newest Python only, so start *Actions → Every Python*
   on `main` as well, which runs the unit tests on the others, and **wait for CI and Every
   Python to pass on that commit**. Then tag it and push the tag:
   `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow asks Forgejo for the
   tagged commit's statuses and refuses a tag on which any test leg, the browser job, the
   checks (lint, migrations, catalogues, and the committed stylesheet, icons and scripts),
   the PostgreSQL job or the security audit are not a success, or on which a supported Python has no passed leg (`release_tools.py check vX.Y.Z --ci` is the same question, from a
   terminal); the image workflow asks it again before building a layer (#233).
6. Once the release exists, start *Actions → Image* for the tag, from the tag. It builds
   and scans the image, pushes `X.Y.Z`, `X.Y` and `latest`, asks the registry for all
   three the way `docker pull` would, and attaches the image's bill of materials to the
   release as `postulo-X.Y.Z-image-sbom.cdx.json`. A red run *after* the push is a
   published image with something after it undone; the run summary says which.
7. Afterwards, from anywhere:
   `python scripts/release_tools.py verify-image vX.Y.Z --registry <host> --image postulo/postulo`
   is the same check, for whoever would rather know than trust (#251).

The `release` workflow does the rest, and it is one job: it refuses a tag that disagrees
with the code or the changelog or whose CI did not pass, builds the sdist and the wheel,
and creates the Forgejo release with the changelog section as its notes.

### Versions of official plugins

A plugin the project publishes -- postulo-mcp, postulo-paperless, the rest under the
`postulo` organisation -- carries the version of the Postulo it was released beside:
postulo-mcp 0.3.0 ships with Postulo 0.3.0. Somebody who knows which Postulo they run
knows which plugin to install, without a compatibility table. The patch is the plugin's
own: 0.3.4 is the fifth fix to the 0.3 plugin and implies no core release.

What the installer enforces is the **major**, through `requires_postulo` in the catalogue:
an official plugin declares `>=0.3,<1.0` and keeps installing across core's minors until
the next major. During 0.x that is loose on purpose -- 0.x is where breaking changes land
in the minor -- so a plugin a core release actually breaks raises its own floor (`>=0.6`)
and says so in its changelog. That is the mechanism, not the installer. The two browser
extensions carry the same number in `package.json`; nothing installs them from a
catalogue, so nothing enforces it there. `postulo-templates` is not a package and carries
no version at all, deliberately: a template pack is copied, not installed.


### Publishing an official plugin

The official catalogue is `catalogue/official/index.json` and its detached signature
`index.json.sig`, served from the raw URL of `main` (`provenance.OFFICIAL_URL`) to any
instance whose administrator switched the *official* repository on. Nothing edits the index
by hand: `plugins.toml` says what is listed, and `scripts/official_catalogue.py` does the rest.

1. Tag the plugin's release and attach its wheel (`uv build --wheel`) to the Forgejo release.
   The wheel is what is signed, so attach the file you built, not a rebuild of it.
2. Add the release to `catalogue/official/plugins.toml`: the address of that asset, and
   `requires_postulo` (`>=0.3,<1.0`, as above).
3. `python scripts/official_catalogue.py build --wheels <dir with the wheels>` writes the index
   with each checksum computed from its file.
4. `POSTULO_CATALOGUE_KEY=<key> python scripts/official_catalogue.py sign` writes the signature.
   The key is the Ed25519 private key in the project's password manager (*Postulo official
   catalogue signing key*), and `sign` refuses any key but the one `provenance.OFFICIAL_KEY` names.
5. `python scripts/official_catalogue.py verify --wheels <dir>` is what an instance runs;
   `tests/test_official_catalogue.py` runs it too. Commit `plugins.toml`, the index and the
   signature together.

A leaked key means a new key: add it to `provenance.OFFICIAL_KEYS`, write a migration that gives
the official row the new one, and sign again. An instance that has not upgraded keeps trusting what
the old key signed, and no more.

Every push to `main` that CI passes builds an image and publishes it as **`:dev`**,
alongside a pinnable `:<version>-dev.<short sha>` — `dev-image.yml`. The job starts with the
push and waits for the commit's CI (`scripts/release_tools.py wait-ci`), so a commit the
tests failed on is never published, and the build does not compete with the tests for the
host. It exists so a change can be run somewhere real before it is in a release, and it is
**not** a release: unsupported, never `:latest`, and free to change a database in ways a
release will not. Quote the pinned tag in a bug report; `:dev` moves and says nothing about
what somebody was running.

It is scanned by the same `scripts/scan-image.sh` a release is, because a dev image somebody
runs against their own applications is an image. It is built for `linux/amd64` and
`linux/arm64`, as the release is, because the instance these images exist to be run on is a
Raspberry Pi — an amd64-only dev image would be one nobody could deploy.

**Old ones are pruned.** Every push adds a pinned tag, so the job ends by running
`scripts/prune-dev-images.py`: the newest five pinned dev tags stay (`DEV_IMAGES_KEPT` in the
workflow) and the rest go, together with the per-architecture manifests only they referenced
— a manifest nothing names still holds its layers. Releases, `latest` and `dev` are never
candidates, and nothing untagged that the run did not itself orphan is touched. Run it by
hand with `--dry-run` to see what it would do; it needs `FORGEJO_USER` and `FORGEJO_TOKEN`
in the environment, never on the command line.

**Why this may run on a push when the release image may not.** The runner answering the
`docker` label is a *second runner instance registered to this repository alone*, so Forgejo
will not schedule another repository's jobs onto it. That is what makes an automatic trigger
acceptable — enforced by the instance, rather than by every workflow author remembering not
to. Re-read that before widening the trigger.

**The container image is a separate, deliberate act.** `image.yml` is started by hand, and
it needs a runner advertising the `docker` label plus `REGISTRY_USER` and `REGISTRY_TOKEN`
as secrets. It is not on the tag trigger, because Forgejo schedules a job before it evaluates the condition that would
skip it: a job asking for a label no runner advertises queues for ever and its run never
finishes, which is how v0.2.0's release came to look unfinished long after it was published
(#81). A workflow nobody starts cannot queue. Without a docker runner,
`scripts/check-image.sh` builds and checks the image wherever there is a Docker daemon.

**The image is scanned before it is pushed.** `scripts/scan-image.sh` runs Trivy and Grype
over it, and `image.yml` calls that same script rather than describing the intent a second
time. Two things about how it is set up are deliberate:

- **Both scanners.** They disagree usefully. On the image that produced #155 and #157, Grype
  found the only actionable Debian update, which Trivy did not mark fixable; Trivy found the
  Python packages and a leftover uv cache, which Grype's deb-and-binary scan did not see at
  all. Running one and believing it would have missed half of it.
- **The gate is fixable findings, not severe ones.** Six CRITICALs with no fix available is
  the normal state of a Debian base image, and a build that fails every day for reasons
  nobody can act on gets switched off within a fortnight. What stops a push is something
  somebody can do something about.

The unfixable half is reported rather than hidden, along with the secret and misconfiguration
scans — a scan that records only its failures throws away the half saying the image is in the
state you think it is. A release run attaches the image's CycloneDX bill of materials to
the release, `postulo-X.Y.Z-image-sbom.cdx.json`, which is more use to somebody self-hosting
Postulo than this run's verdict: it lets them scan the release later, against a database
that does not exist yet. On the release rather than as a run artifact, because this Forgejo
refuses `upload-artifact@v4` -- "not currently supported on GHES" -- and v3's artifacts
expire; the refusal, sitting before the push, is how v0.3.0 came to be released with no
image at all (#251). `tests/test_image_build.py` keeps v4 out of every workflow.

Both scanners download a vulnerability database, so the step needs the network.

**Three outcomes, not one red step.** The script exits 0 when nothing fixable was found, 1
on fixable findings — the gate — and 2 when the scan did not complete: a scanner that failed
to run, a bill of materials that came back empty. The last is not a finding and must not
read as one; it says nothing about the image, and says so. `.scan/verdict.txt` holds which
of the three it was (`clean`, `findings`, or `incomplete: <why>`), and both image workflows
put it at the top of their run summary. `tests/test_scan_image.py` drives all three through
a fake `docker`, so the reading of the scanners is tested without running them (#192).

### Giving a runner the `docker` label

**Not `docker:host`, if the runner is itself a container.** That was the advice here until
#190 and it does not work: `host` runs the job *inside the runner container*, and
`code.forgejo.org/forgejo/runner` is Alpine with no node and no docker CLI, so
`actions/checkout` — a JavaScript action — fails before anything reaches the daemon. The
recipe assumed a runner installed on the host.

What works, and what `ouranos` runs, is a **container label pointing at an image that
already has the tools**, with the socket mounted into job containers:

```yaml
runner:
  labels:
    - "docker:docker://catthehacker/ubuntu:act-latest"
container:
  docker_host: "automount"
```

`catthehacker/ubuntu:act-latest` ships node, git, the docker CLI and buildx, so there is no
custom image to build and keep current. `automount` puts `/var/run/docker.sock` into the job
container.

**Use a second runner instance, not a label on the one you have.** This is the part that
matters, and it is not obvious:

- **`container.docker_host` is per runner instance, not per label.** Setting `automount` on
  a runner that also serves the ordinary `ubuntu-*` labels hands the socket to *every* job
  it runs — and Docker is root on that machine. There is no way to scope it to one label.
- **Register the second runner to one repository** (`--scope owner/repo`). Forgejo then
  refuses to schedule anything else onto it. That is a stronger guarantee than "nothing
  schedules onto this label by itself", because it is enforced by the instance rather than
  by every workflow author remembering.

With that, an automatic trigger becomes reasonable: `dev-image.yml` builds on every push to
`main`. Without it — a shared runner with `automount` — it is not, and the trigger is the
first thing to reconsider if the runner arrangement ever changes.

QEMU binfmt is still needed on the **host** for the `linux/arm64` half of both images:
`docker run --privileged --rm tonistiigi/binfmt --install all`.

### Starting the image workflow

Two refs are involved and they are not the same one, which is worth saying because getting
it wrong produces an error that explains nothing:

```
GetWorkflowFromCommit, workflow not found
```

Forgejo reads a dispatched workflow **from the ref you dispatch it from**, and `image.yml`
has only existed since after v0.2.1. Choosing a tag in the branch selector therefore looks
for the file in that tag's tree, does not find it, and says the above. So:

- **dispatch from a branch that has the file** — the branch selector;
- **name the tag to build in the `tag` input** — the box on the form.

The workflow's own checkout uses `ref: ${{ inputs.tag }}`, so the workflow comes from the
branch while the code built comes from the tag.

Two secrets have to exist first, on the repository or its organisation: `REGISTRY_USER` and
`REGISTRY_TOKEN`, the latter a token with `write:package`. Without them the sign-in step
fails on an empty password, which the log reports as a login failure rather than as a
missing secret.

To check a runner works before a release depends on it, dispatch from a branch and give it
the current release's tag. That builds something real, publishes tags that are true, and
does not need a new tag cut for the purpose.

### Scanning the image

Nothing scans the image yet — that needs a runner that can build one, and is #156. Until
then it is a step somebody does, and this is the step:

```sh
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    aquasec/trivy image --ignore-unfixed postulo:latest
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    anchore/grype postulo:latest --only-fixed
```

**`--ignore-unfixed` / `--only-fixed` is the point, not a convenience.** A Debian stable
base image carries dozens of HIGH and several CRITICAL findings with no fix available —
that is the normal state of Debian stable, where the security team triages a great many as
no-DSA. A gate on severity is therefore red every day for reasons nobody can act on, and is
switched off within a fortnight. A gate on *fixable* findings is one somebody can clear, and
on the image that produced #155 it was a single line.

**Run both.** They disagreed usefully: Grype found the only actionable Debian update, which
Trivy did not mark fixable at all; Trivy found the Python packages and a leftover uv cache,
which Grype's scan did not see. They also disagree about severity, because Trivy prefers the
distribution's rating of how a CVE affects *its* build and Grype leans on the NVD's. Neither
number is the number, and anything written into a pipeline has to say which tool it means.

**Write down the clean half too.** The scan that produced #154 and #155 also reported zero
secrets and zero misconfigurations, and that is the result that gets forgotten when only the
bad news is recorded.

A fixable finding in a Debian package usually means the image needs rebuilding without a
cache rather than a code change: `apt-get upgrade` runs at build time (see `docker/Dockerfile`
and *Configuration → The image and Debian's updates*), so a fresh build takes whatever Debian
has published since.

## A plugin that needs consent

`FieldSpec` covers everything a person can type. For a provider that wants OAuth instead,
declare what is being asked for and Postulo conducts the round trip:

```python
from postulo.plugins.base import Consent, FieldSpec


class MyPlugin:
    def config_fields(self) -> list[FieldSpec]:
        # The client id and secret are still fields: they identify the *instance* to the
        # provider, and an operator registers them by hand.
        return [
            FieldSpec("client_id", "Client id"),
            FieldSpec("client_secret", "Client secret", type="password", secret=True),
        ]

    def needs_consent(self) -> Consent:
        return Consent(
            authorise_url="https://provider.example/authorise",
            token_url="https://provider.example/token",
            scopes=("https://provider.example/auth/send",),
            provider="Provider",
            extra={"access_type": "offline"},  # Google needs this to issue a refresh token
        )
```

Postulo takes it from there: the redirect, the signed state, the code exchange, the encrypted
tokens, and a refresh before every use. Ask for the token with
`postulo.plugins.consent.access_token(connection)` — never keep one yourself, and never read
the stored secrets directly, because refreshing is the part that has to happen at the moment
of use.

**Ask for the narrowest scope that does the job.** The scopes are shown to the person on the
page before they agree, which is the point: a list nobody can read is a list nobody consented
to.

**Do not reach for the sign-in tokens.** allauth holds one for anybody who signed in through a
provider, and it carries the scopes asked for at sign-in. Reusing it would mean asking for a
mail scope at sign-in on the chance it might be useful later, which is precisely the
over-broad consent this project should not teach — and allauth holds one token per account per
application, so a second grant has nowhere to sit beside the first.

## A plugin that owns a table

Most plugins own nothing. A source reads a page, an importer reads a file, a transport carries
a message; none of them keeps anything, and any of them can be moved out of core without a
further thought. **A plugin that owns a model is a different animal**, and there is one rule.

> A plugin that owns a table may not be uninstalled while that table holds anything.

Postulo refuses, says how many records are in the way, and leaves both the package and the
data alone. Two other answers were open and neither is safe. *Uninstall and keep the table*
leaves data nothing can read, export or restore — the failure the export exists to prevent.
*Uninstall and delete, behind a confirmation* makes removing a package a data-destroying act:
somebody swapping a plugin for a newer build of the same plugin loses everything it held, and
Postulo promises in thirty-nine languages that switching a plugin **off** deletes nothing.
Putting *uninstall* on the other side of that promise is a distinction nobody holds in their
head at the moment it matters.

**Off and uninstalled are now different acts.** Off keeps everything and offers nothing.
Uninstalled takes the code away, and is refused while there is anything to take away with it.

What a plugin that owns a model has to do:

```python
class MyPlugin:
    name = "my-plugin"
    #: Django labels. Postulo counts these before letting the package go.
    owns_models = ("my_plugin.Thing",)

    def export_for(self, subject) -> list[dict]:
        """What you hold about this subject, for their archive. See below."""
        return [{"what": row.what} for row in Thing.objects.filter(owner=subject)]

    def erase_for(self, contact) -> int:
        """Remove what you hold about this contact, and say how many rows went."""
        removed, _by_model = Thing.objects.filter(about=contact).delete()
        return removed
```

**`export_for` is not optional in spirit.** A plugin that owns a person's data and cannot put
it in their archive gets its models *named* in that archive under `not_carried`, because an
archive that is quietly incomplete is discovered when somebody restores it, and one that says
which part is missing is discovered while they still have the original. Write the method.

**It is asked about two kinds of subject.** The account holder, for their archive; and one of
their contacts, for the document of what the instance holds on that person and before two
contacts are merged. Answer an empty list for a subject you hold nothing about: a plugin that
cannot say is treated as one that holds something.

**`erase_for` is what lets a person be erased.** Where the data-protection feature is on,
deleting a contact asks every plugin that owns rows to remove its rows about them and to
return how many went. A plugin that holds rows about the contact and has no `erase_for`, or
whose `erase_for` raises, stops the erasure: nothing is removed, not even what another plugin
had already removed, and whoever asked is told which plugin is in the way. A report must not
say somebody is gone while rows about them remain. Write this method too.

**Each call runs in a savepoint of its own, and a failure is logged with its traceback.** An
exception out of your query undoes your own work and nothing else, and the request around it
carries on with the careful answer in your place: not carried, holds something, could not
erase. The log is where you find out which it was.

**A plugin that owns a table, or pages, has to be built into the image.** Django needs the app
in `INSTALLED_APPS` to see the model at all, and `INSTALLED_APPS` is fixed when the process
starts: there is no entry-point group that adds to it, nothing mounts a plugin's URLs, and
`migrate` has already run by the time anything looks at a plugin. So a package installed from a
wheel or a catalogue brings entry points and nothing else — a source, an importer, a notifier,
an outbox, a store, a sync, a transport, a feature — and one with its own tables or its own
pages belongs in `src/postulo/`, with its app in `INSTALLED_APPS` and its migrations beside it.
It is still a plugin in every other sense; it is just one that ships inside.

**Ship your own migrations.** Because the package cannot be uninstalled while its table holds
anything, the table is always empty when the app goes — so its migrations reverse cleanly and
there is never a migration referring to a module that no longer imports.

**Emptying the table is your job to offer.** The refusal tells somebody to empty it from the
plugin's own pages; those pages are yours, and while the plugin is installed is exactly when
they can be asked.

## Licence

Contributions are accepted under the [AGPL-3.0-or-later](LICENSE) licence that covers
the project.

Contributors keep the copyright in what they write. The notice in the README and the other files, *Copyright (C) 2026 Postulo contributors*, names all of them together, and the list of who they are is the repository's git history.
