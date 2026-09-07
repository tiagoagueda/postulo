# Contributing to Postulo

Thank you for considering it. Postulo is developed on
[Forgejo](https://source.tiagoagueda.com/postulo/postulo); the GitHub repository is a
read-only mirror, so please open issues and pull requests on Forgejo.

## Getting set up

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

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
uv run pytest
uv run manage.py makemigrations --check --dry-run
npm run build:css            # only if you touched assets/css/
```

Continuous integration runs all of the above across Python 3.12, 3.13, and 3.14, plus
`manage.py check --deploy` against production settings, and it fails if the committed
stylesheet has drifted from its source.

There is also a browser test of the critical path (sign in, capture, review, board, record
what was sent, export), which is left out of the default run because it needs a browser:

```sh
uv sync --group e2e
uv run playwright install chromium
uv run pytest -m e2e
```

CI runs it on every push. If you change a page on that path — the header, the capture
review, the board, the export — run it before opening the pull request; it is the test that
notices when steps stop joining up.

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

An `<option>` element can hold text and nothing else, so a `<select>` of countries cannot
show flags in its list. Where one is needed, put it beside the closed select and let each
option carry its own flag's URL in a `data-` attribute; see `partials/phone_widget.html`.
Do not replace a native select with a custom listbox to make room for pictures.

## Tables

A table wider than the card it sits in scrolls sideways, and the settings area gives its
card about 730 pixels however wide the window is — so a table that does not fit scrolls on
a desktop, not only on a phone. Measure before assuming otherwise:
`card.scrollWidth - card.clientWidth` in the browser, at 1440 as well as at 390.

Two things keep one honest:

- **Row actions are a menu**, not a row of buttons. Four buttons with their labels spelled
  out made one column of *Server settings → People* 335 pixels wide — larger than the email
  column, and holding no information at all. Use the `<details data-menu>` disclosure the
  account menu uses; the existing script already closes it when the pointer goes elsewhere.
- **`table-cards`** turns each row into a card below the `md` breakpoint, where columns have
  nowhere to go. Give every cell a `data-label` naming its column, except the one that is
  the card's heading.

`table-cards` works by making table elements blocks, and **that strips the table semantics a
screen reader navigates by** — "Administrator" stops being the Role of a row and becomes a
loose word. So a table using it must carry `role="table"`, `role="rowgroup"`, `role="row"`,
`role="columnheader"` and `role="cell"` explicitly. On a wide screen those roles are what
the elements already are and change nothing; on a narrow one they are the only thing holding
the meaning together. `server/people.html` is the worked example.

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
  in templates. Untranslatable strings are treated as bugs.
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

## Documentation

User documentation lives in [wiki/](wiki/) and is published to the project wiki with
`scripts/publish-wiki.sh`. Editing it here rather than in the wiki interface means
documentation changes are reviewed alongside the code that caused them.

Please keep it honest: the wiki says plainly what is not built yet, and a page that
describes a feature which does not exist is a bug.

## Translations

Postulo is written in British English and translated from there. French and Portuguese
catalogues exist and are waiting for contributors — see
[docs/TRANSLATING.md](docs/TRANSLATING.md).

## Keeping dependencies current

Once a month, or when the `security` job says so: `uv lock --upgrade`, run the suite,
read the changelogs of anything that moved a major version, and commit the lock file on
its own. `docs/THREAT-MODEL.md` says who the attackers are and which rules follow; a pull
request that touches a boundary answers to it.

## Writing a changelog entry

An entry says **why**, not what. The diff already says what. Each goes under *Unreleased*,
in one of six sections, each marked so the file can be scanned rather than read:

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

1. Move the *Unreleased* entries in `CHANGELOG.md` under a new `## [X.Y.Z] — YYYY-MM-DD`
   heading, and leave an empty *Unreleased* above it.
2. Set the same version in `pyproject.toml` and in `src/postulo/__init__.py`.
3. `python scripts/release_tools.py check vX.Y.Z` says whether the three agree.
4. Commit, then tag and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The `release` workflow does the rest: it refuses a tag that disagrees with the code or the
changelog, builds the sdist and the wheel, and creates the Forgejo release with the
changelog section as its notes. The image job runs only on a runner with the `docker`
label and the repository variable `BUILD_IMAGE` set to `true` (with `REGISTRY_USER` and
`REGISTRY_TOKEN` as secrets), so that without one nothing queues for ever; until then,
`scripts/check-image.sh` builds and checks the image wherever there is a Docker daemon.

## Licence

Contributions are accepted under the [AGPL-3.0-or-later](LICENSE) licence that covers
the project.
