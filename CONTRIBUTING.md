# Contributing to Postulo

Thank you for considering it. Postulo is developed on
[Forgejo](https://git.tiagoagueda.com/tiagoagueda/postulo); the GitHub repository is a
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
```

Continuous integration runs all of the above across Python 3.12, 3.13, and 3.14, plus
`manage.py check --deploy` against production settings.

## House style

- **British English** in code, comments, documentation, and interface text: *organise*,
  *colour*, *licence* (noun), *behaviour*.
- **Wrap every user-facing string** in `gettext` / `gettext_lazy`, or `{% translate %}`
  in templates. Untranslatable strings are treated as bugs.
- **Every user-owned model** inherits the shared owned-model base and is filtered by
  owner in every query. Cross-account data leaks are the one bug class we test for
  explicitly, so new models need a test proving isolation.
- **Personal documents are private.** Never serve uploaded media directly.
- Keep commits focused, and describe *why* in the message rather than *what*.

## Translations

Postulo is written in British English and translated from there. French and Portuguese
catalogues exist and are waiting for contributors — see
[docs/TRANSLATING.md](docs/TRANSLATING.md).

## Licence

Contributions are accepted under the [AGPL-3.0-or-later](LICENSE) licence that covers
the project.
