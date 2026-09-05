# Contributing to Postulo

Thank you for considering it. Postulo is developed on
[Forgejo](https://source.tiagoagueda.com/tiagoagueda/postulo); the GitHub repository is a
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
  the page with the keyboard alone before calling it done.
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
