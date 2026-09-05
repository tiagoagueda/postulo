# Translating Postulo

Postulo's source language is **British English (`en-gb`)**. Every other locale is a
translation of it.

| Locale  | Language                    | Status                  |
| ------- | --------------------------- | ----------------------- |
| `en-gb` | English (United Kingdom)    | Source language         |
| `fr-fr` | French (France)             | Awaiting a contributor  |
| `pt-pt` | Portuguese (Portugal)       | Awaiting a contributor  |

Note that `pt-PT` is European Portuguese, not Brazilian. If you would like a `pt-BR`
catalogue, open an issue — adding a locale is a one-line change plus a directory.

## Which languages, and when

The source language is British English. The 0.2.0 release aims to ship a catalogue for
every official language of the European Union: Bulgarian, Croatian, Czech, Danish, Dutch,
Estonian, Finnish, French, German, Greek, Hungarian, Irish, Italian, Latvian, Lithuanian,
Maltese, Polish, Portuguese, Romanian, Slovak, Slovene, Spanish and Swedish. A first draft
may be machine-assisted and is marked *fuzzy* until a speaker has read it, so a language is
usable on day one and honest about its state. Later releases extend the set in phases: the
rest of the European continent, then Africa, then Asia, then the remaining world. If your
language is not in the current phase, a catalogue for it is still welcome.

## Adding or updating a translation

Extracting messages requires GNU `gettext` (`sudo apt install gettext`, or the
[Windows binaries](https://mlocati.github.io/articles/gettext-iconv-windows.html)).

```sh
# Refresh the catalogues after strings change
uv run manage.py makemessages --locale fr_FR --locale pt_PT --ignore .venv

# Edit locale/<locale>/LC_MESSAGES/django.po in a text editor or a tool such as Poedit

# Compile before running the application
uv run manage.py compilemessages
```

Compiled `.mo` files are build artefacts and are not committed; only `.po` files are.

## Guidance for translators

- Translate meaning, not words. "Ghosted" describes an employer that stopped replying;
  render it however your language expresses that, even if the wording differs.
- Keep placeholders such as `%(company)s` intact and in a natural position for your
  language.
- Domain terms worth agreeing on before you start: *application*, *posting*, *CV*,
  *cover letter*, *screening*, *offer*, *withdrawn*. Please stay consistent across the
  catalogue.
- Use the formal or informal register that fits your language's convention for
  professional software. For French, Postulo uses *vous*.

## Adding a new language

1. Add the locale to `LANGUAGES` in `src/postulo/config/settings/base.py`.
2. Create `locale/<locale>/LC_MESSAGES/`.
3. Run `makemessages` for it, translate, and open a pull request.
