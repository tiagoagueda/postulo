# Translating Postulo

Postulo's source language is **British English (`en-gb`)**. Every other language is a
translation of it, kept as a `.po` catalogue under `src/postulo/locale/<locale>/LC_MESSAGES/`
and compiled to the `.mo` Django reads at build time.

## Which languages, and when

Postulo speaks every official language of the European Union: Bulgarian, Croatian, Czech,
Danish, Dutch, English, Estonian, Finnish, French, German, Greek, Hungarian, Irish,
Italian, Latvian, Lithuanian, Maltese, Polish, Portuguese, Romanian, Slovak, Slovene,
Spanish and Swedish. Later releases extend the set in phases: the rest of the European
continent, then Africa, then Asia, then the remaining world. If your language is not in
the current phase, a catalogue for it is still welcome — adding one is described below.

Note that `pt-PT` is European Portuguese and `fr-FR` is the French of France. A `pt-BR`
or `fr-CA` catalogue is a directory and a line, if someone wants to keep it.

## Drafts, and what reviewing one means

Every catalogue was first filled by machine-assisted translation, so that a language is
usable on day one rather than English in the gaps. Each such entry carries the flag
`draft`:

```po
#: src/postulo/templates/applications/application_list.html:64
#, draft
msgid "Applications"
msgstr "Bewerbungen"
```

The language picker says *machine translation, awaiting review* beside a language while
any of its entries is a draft, and *N % translated* while any is missing. **Reviewing a
draft means reading it and deleting the flag**: if the translation is right, remove
`draft`; if it is wrong, fix it and remove `draft`. That is the whole job, and it can be
done a few strings at a time. A translation that needs a second opinion can carry
`fuzzy` instead, which — as with every gettext tool — keeps it out of the compiled
catalogue until someone settles it.

How far along each language is:

```sh
uv run python scripts/messages.py stats
```

## The tool

GNU gettext is not needed. `scripts/messages.py` does in plain Python what `makemessages`
and `compilemessages` shell out to `xgettext` and `msgfmt` for:

```sh
uv run python scripts/messages.py extract          # refresh every catalogue from the source
uv run python scripts/messages.py extract --check  # fail if a catalogue is out of date (CI)
uv run python scripts/messages.py check            # placeholders and plural forms agree (CI)
uv run python scripts/messages.py compile          # write the .mo files Django loads
uv run python scripts/messages.py stats [--write]  # progress; --write refreshes status.json
```

`extract` keeps every existing translation and its flags, adds a slot for each new string
and drops the ones the source no longer has. `check` refuses a translation that lost or
invented a `%(placeholder)s`, and a plural entry with the wrong number of forms for its
language. Both run on every push, so a pull request that adds a string without a slot for
it, or a translation that would raise at render time, does not get in.

Compiled `.mo` files are build artefacts and are not committed; the container image, the
release wheel and the test suite each compile their own.

## Editing a catalogue

Any text editor works; [Poedit](https://poedit.net/) or a similar tool shows the source
beside the translation and knows the plural forms. Either way:

1. Edit `src/postulo/locale/<locale>/LC_MESSAGES/django.po`.
2. `uv run python scripts/messages.py check`, then `stats --write` so the picker's note
   about the language stays true.
3. Open a pull request. A reviewer who speaks the language is ideal; one who can read a
   diff and run the checks is enough.

## One distinction worth care: kinds of letter

Postulo has four kinds of letter, and two of them are a trap for translators. In French
and Portuguese, *lettre de motivation* and *carta de motivação* are the everyday words for
what English calls a **cover letter** — one page, addressed, about one posting. Postulo's
**motivation letter** is a different document: longer, sectioned, about the person and
their reasons, usually with no addressee block, and the norm for academic posts, EU
institutions and NGOs.

Translating both with the same phrase makes the two kinds indistinguishable in the
interface. Where your language has one everyday word, give the cover letter that word and
find a longer, plainer phrase for the motivation letter — or the other way round if that
reads better. The interface tells them apart by their shape, and the starter text for each
kind shows it, so a reader who sees both will not be confused for long; the names should
help rather than fight that.

## Guidance for translators

- Translate meaning, not words. *Ghosted* describes an employer that stopped replying;
  render it however your language expresses that, even if the wording differs.
- Keep placeholders such as `%(company)s` intact and in a natural position for your
  language. A plural form may drop the count where the language does ("one company").
- Domain terms worth agreeing on before you start: *application*, *posting*, *listing*,
  *CV*, *cover letter*, *screening*, *offer*, *withdrawn*, *capture*. Stay consistent
  across the catalogue; the drafts already are, so a term you change is worth changing
  everywhere.
- Use the register your language's convention for professional software expects. French
  uses *vous*, German *Sie*, Spanish *usted*-free impersonal forms where natural.
- Dates, numbers and the first day of the week come from Django's format definitions for
  the language, not from the catalogue.

## Adding a new language

1. Add the code and the language's own name for itself to `NATIVE_NAMES` in
   `src/postulo/core/languages.py`, and its gettext plural rule to `PLURAL_FORMS`.
2. `uv run python scripts/messages.py extract` creates the catalogue.
3. Translate, `check`, `stats --write`, and open a pull request.

## Plugins

A plugin's strings are the plugin's to translate: its `locale/` directory sits beside the
package and Postulo reads it when the plugin loads. See `docs/PLUGINS.md`.
