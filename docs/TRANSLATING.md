# Translating Postulo

Postulo's source language is **British English (`en-gb`)**. Every other language is a
translation of it, kept as a `.po` catalogue under `src/postulo/locale/<locale>/LC_MESSAGES/`
and compiled to the `.mo` Django reads at build time.

## Which languages, and when

Postulo speaks every official language of the European Union: Bulgarian, Croatian, Czech,
Danish, Dutch, English, Estonian, Finnish, French, German, Greek, Hungarian, Irish,
Italian, Latvian, Lithuanian, Maltese, Polish, Portuguese, Romanian, Slovak, Slovene,
Spanish and Swedish, plus **Brazilian Portuguese** beside the European. It also speaks the
continent beyond the Union: Albanian, Armenian, Basque, Bosnian, Catalan, Galician,
Georgian, Icelandic, Luxembourgish, Macedonian, Norwegian Bokmål, Serbian, Turkish,
Ukrainian and Welsh. The language picker in Settings is the authority on what is offered
today. If your language is not in the current phase, a catalogue for it is still welcome —
adding one is described below.

The set grows in phases, one per release (#43):

| Release | Languages | State |
| --- | --- | --- |
| 0.2.0 | The 24 official languages of the European Union | Complete, machine-drafted |
| 0.3.0 | The rest of Europe (#118) — 15 languages | Complete, machine-drafted |
| 0.3.0 | Africa (#70) — 29 languages | Catalogues created, awaiting translation |
| 0.4.0 | Asia and South America (#71) | Not started |
| 0.5.0 | The rest of the world (#72) | Not started |

"Every language of Africa" is some two thousand of them, so the rule drawn for 0.3.0 is
**a language with official or national status in at least one African state, plus the
cross-border lingua francas that outrank most of those in speakers**: Afrikaans, Akan,
Amharic, Arabic, Bamanankan, Chichewa, Eʋegbe, Hausa, Igbo, isiNdebele, isiXhosa, isiZulu,
Ikinyarwanda, Lingála, Malagasy, Afaan Oromoo, Pulaar, Sesotho, Setswana, chiShona,
siSwati, Soomaali, Kiswahili, Taqbaylit, ትግርኛ, Tshivenḓa, Wolof, Xitsonga and Yorùbá.
French, Portuguese, English and Spanish already carry a great deal of the continent, so
the gap was smaller than the map suggests.

**A language is offered once somebody has begun its catalogue, and not before.** All 29
African catalogues exist and are empty; the language picker does not list them yet,
because offering somebody their own language and handing them an English interface is a
promise with nothing behind it. Translate one string and the language appears, with its
completion percentage beside its name.

Beyond the Union, two things stop being tidy, and both are handled rather than
special-cased. A language may be at home somewhere that is not a state — Catalan's flag is
not Spain's, which already stands for Spanish on the same list; it is `ES-CT`, Catalonia's
own, and Basque's is `ES-PV`, Galician's `ES-GA`, Welsh's `GB-WLS`. So the table holds ISO
3166-2 subdivisions as well as countries. And a language may bring its own script: Greek,
Cyrillic, Georgian and Armenian are all here, and every option in the picker carries `lang`
so a screen reader says each name in its own language.

## A variant of a language already spoken

`pt-br` is the first case of two regions of one language both being offered, and it was
added a particular way that the next one should copy.

**Seed from the sibling, do not translate again.** Every string in `pt-pt` had already been
translated once by somebody thinking about this application; what a variant wants is that
work adapted, not a second independent pass from English. Each seeded entry carries the
`draft` flag, and there it means something precise: *this came from the other catalogue and
nobody who speaks this one has read it*.

**Then adapt only what is unambiguous.** A mechanical pass changed the terms where the two
are simply different words — *ficheiro* to *arquivo*, *palavra-passe* to *senha*, *definições*
to *configurações* — word-boundary anchored and case-preserving. Two near-misses are worth
knowing about, because the next variant will meet their equivalents: `rato` is Brazilian
*mouse* and also the tail of *contrato*, and `carregar` appears only inside *descarregar*.
Where the Portuguese was ambiguous the **English msgid** settled it: *Guardar* is sometimes
"keeps" and sometimes "Save", and only the msgid knows which.

**And leave the rest.** `ligação` means both a *Connection* and a *link* here; the gerund,
clitic placement and the choice of register are grammar rather than glossary. Those are a
speaker's to fix, which is what the `draft` flag is for.

**Check the plural rule rather than copying it.** Brazilian Portuguese treats zero as plural
— *0 candidaturas* — where European Portuguese says *0 candidatura*. That is one line, and
getting it wrong makes every count on every page ungrammatical.

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

The language picker is a **disclosure** holding a list, not a dropdown: closed it is one
line showing the language in use, and open it is every language grouped by how far along it
is — *read by a speaker*, *written, not yet read by a speaker*, or *still being translated*
with the percentage beside the name.

It is not a `<select>` because one cannot do what this needs. An `<option>` takes `lang` and
nothing inside it, so the name could not be marked as being in its own language, the flag
could not be hidden from a screen reader, the symbol for how a translation was made would be
announced as part of a string claiming to be German, and the percentage would have nowhere to
go but inside that name. Here every one of those sits outside the `lang`-marked span, in the
interface language, with words read out beside each symbol and the legend on the page (#119).

The wording is *written, not yet read by a speaker* rather than anything about machines. A
variant seeded from a sibling catalogue and adapted by hand is not machine-translated, and
`pt-BR` is exactly that; what is true of every language in that group is that no speaker has
read it yet. **Reviewing a
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

Every command walks **every set of catalogues**, not only Postulo's own: a plugin Postulo
ships carries its own, beside its package. `extract` writes each string to the set that
owns the file it was found in, `check` and `stats` read all of them, and `compile` writes
every `.mo` in one pass — so the image still builds its catalogues in one step, with no
per-plugin build to add or forget. `stats` and `status.json` report the sum, because
"português is complete" has to mean the interface somebody will see rather than the part
of it that happens to live in core.

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
2. Add the place whose flag belongs beside it to `FLAG_COUNTRIES` in the same file — an
   ISO 3166-1 country, or a 3166-2 subdivision where the language is at home somewhere that
   is not a state. Choose it deliberately rather than deriving it from the code, because a
   language is not a country: Spanish is not only Spain and Arabic is not one flag. **Leave
   it out where there is no honest answer**; the picker copes with a blank. Then add the
   code to `assets/flags.txt` and run `npm run sync:flags`.
3. **If the language is read right to left**, add its subtag to `RTL` in the same file.
   That one list is what both the interface and a rendered document read, so a language
   added there is laid out correctly everywhere at once. The interface layout itself needs
   no work — that was done in #67 and is held by a lint and a browser suite that visits the
   application in a right-to-left language.
4. `uv run python scripts/messages.py extract` creates the catalogue.
5. Translate, `check`, `stats --write`, and open a pull request.

## Plugins

A plugin's strings are the plugin's to translate: its `locale/` directory sits beside the
package and Postulo reads it when the plugin loads. See `docs/PLUGINS.md`.

That is true of the plugins Postulo ships as well. Their catalogues live beside their
packages — `src/postulo/plugins/builtin/locale/` for the two built-in capture sources —
and are edited, checked and reviewed exactly like the ones in `src/postulo/locale/`. The
completeness rule for the European Union languages applies to each set separately, so a
built-in cannot quietly fall behind.

Postulo's own catalogue is read before any plugin's, so where both translate the same
English string, Postulo's rendering is the one shown.
