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

## Licence

Contributions are accepted under the [AGPL-3.0-or-later](LICENSE) licence that covers
the project.
