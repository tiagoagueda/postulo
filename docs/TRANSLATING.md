# Translating Postulo

Postulo's source language is **British English (`en-GB`)**. Every other language is a
translation of it, kept as a `.po` catalogue under `src/postulo/locale/<locale>/LC_MESSAGES/`
and compiled to the `.mo` Django reads at build time.

## What a language code is

A language is named by a **BCP 47 tag in its canonical form**: `pt-BR`, `en-GB`, `sr-Cyrl`,
`de`. The language in lower case, a script with a capital, a region in capitals, joined by
hyphens. Tags compare without regard to case, so Postulo takes a code in however a file, a
page or an address writes it, and writes it one way: `pt-BR`, never `pt-br`, in everything it
stores, sends and shows (#337).

**A catalogue's directory is not a tag.** It is the gettext name of the locale, made from
the tag by the tool: `pt-BR` is kept in `pt_BR/` and `sr-Cyrl` in `sr_Cyrl/`, and the
`Language:` header of the file says the same. The underscore is gettext's. It is a
directory's name and that header, and nothing else; nobody types one.

**A tag says its script only where the language is written in more than one.** Serbian
is, and the catalogue here is the Cyrillic one, so it is `sr-Cyrl`. Bulgarian is not, so it
is `bg` and never `bg-Cyrl`. The registry decides which: Postulo's list is held by a test
to a copy of the [IANA language subtag registry](https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry)
kept in `tests/data`, which is the registry without its descriptions. To take a newer one:

```sh
curl -s https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry \
  | grep -vE '^(Description|Added|Comments):|^  ' > tests/data/language-subtag-registry
```

Only the list is held to the registry. What somebody declares about a document, or a file
says about a career, is held to the shape of a tag and no more: it may be in a language
Postulo has never heard of.

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
| 0.5.0 | The rest of the world (#72) | Not started; the process is below |

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

`pt-BR` is the first case of two regions of one language both being offered, and it was
added a particular way that the next one should copy.

**Seed from the sibling, do not translate again.** Every string in `pt-PT` had already been
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
uv run python scripts/messages.py stats [--write|--check]  # progress; --write refreshes status.json, --check (CI) fails if stale
```

`extract` keeps every existing translation and its flags, adds a slot for each new string
and drops the ones the source no longer has. `check` refuses a translation that lost or
invented a `%(placeholder)s`, a plural entry with the wrong number of forms for its
language, and a plural form that drops the count where its rule also covers twenty-one. Both run on every push, so a pull request that adds a string without a slot for
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

## The word for a classification: Type

In the interface, what a person picks from a list to say what sort of thing something is is
called its **Type**: *Type* of company, of letter, of entry, of document. `kind` stays in the
code, the API, the archive and the plugin contract, and never reaches a person. A translator
should use the ordinary word for *type* in their language (French *type*, Portuguese *tipo*)
and not *genre*, *sort* or *género*, which belong to other things; *Typography* is the design
page's heading and is a different word.

## One distinction worth care: types of letter

Postulo has four types of letter, and two of them are a trap for translators. In French
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
  language. A plural form may drop the count only where that form never says anything but
  *one*: French can write "une entreprise", because its first form covers 0 and 1 and
  nothing else. A Slavic, Baltic or Icelandic first form also covers 21, 31 and 101, so it
  must keep `%(counter)s`, or the page says "one application" at twenty-one. `check` refuses
  a form that drops the count where its rule also counts higher.
- Domain terms worth agreeing on before you start: *application*, *posting*, *listing*,
  *CV*, *cover letter*, *screening*, *offer*, *withdrawn*, *capture*. Stay consistent
  across the catalogue; the drafts already are, so a term you change is worth changing
  everywhere.
- Use the register your language's convention for professional software expects. French
  uses *vous*, German *Sie*, Spanish *usted*-free impersonal forms where natural.
- Dates, numbers and the first day of the week come from Django's format definitions for
  the language, not from the catalogue. Nothing in here decides whether the year comes
  first or whether an interview is at 14:00 or at 2 p.m.; if a date reads wrongly in your
  language, that is a bug in Postulo and not a string for you to fix.
- The per cent sign *is* yours. `%(share)s%%` is one string so that French can write
  `%(share)s %%` with the space it needs and Turkish can write `%%%(share)s` with the sign
  in front. The `%%` is a literal per cent sign; keep it doubled.

## Adding a new language

1. Add the code and the language's own name for itself to `NATIVE_NAMES` in
   `src/postulo/core/languages.py`, and its gettext plural rule to `PLURAL_FORMS`. The code
   is a BCP 47 tag in its canonical form, as above; `tests/test_language_registry.py` fails
   on one the registry does not hold.
2. Add the place whose flag belongs beside it to `FLAG_COUNTRIES` in the same file — an
   ISO 3166-1 country, or a 3166-2 subdivision where the language is at home somewhere that
   is not a state. Choose it deliberately rather than deriving it from the code, because a
   language is not a country: Spanish is not only Spain and Arabic is not one flag. **Leave
   it out where there is no honest answer**; the picker copes with a blank. Then add the
   code to `assets/flags.txt` and run `npm run sync:flags`.
3. **If the language is not written in the Latin alphabet**, add the ISO 15924 code of its
   script to `SCRIPTS` in the same file, which is what decides the fonts the image has to
   carry: the registry test fails until it is there, and `tests/test_fonts.py` until a
   font package is recorded for it. **If it is read right to left**, add its subtag to `RTL`.
   That one list is what both the interface and a rendered document read, so a language
   added there is laid out correctly everywhere at once. The interface layout itself needs
   no work — that was done in #67 and is held by a lint and a browser suite that visits the
   application in a right-to-left language.
4. `uv run python scripts/messages.py extract` creates the catalogue.
5. Translate, `check`, `stats --write`, and open a pull request.

## Adding a language without a developer

The aim of the last phase is a process rather than a list: a speaker adds a language
without waiting for a release, and what they add shows up as soon as it is merged (#72).

- **A catalogue is a pull request**, or a translation made on the project's translation
  platform; either way it is the `.po` file under `src/postulo/locale/<locale>/` and
  nothing else. No code needs to change for a language already on the list.
- **A language is offered once one string is translated** (`accounts/forms.py::language_choices`),
  and not before, so a scaffolded catalogue nobody has begun stays invisible.
- **Partial is normal.** A language below 95% sits in the *Partly translated* group of the
  picker with its percentage, and the page says that English shows wherever the translation
  has not reached. Nothing is hidden and nothing is promised.
- **The picker is searchable.** With scripts on, a box above the list narrows it by the
  name in its own language or by the tag; with scripts off the list is whole, grouped by
  how far along each language is.
- **Plugins carry their own catalogues** and are completed, checked and offered the same
  way; a language is as complete as the interface somebody sees, core and plugins together.

## The wiki in other languages

Decided in #352: **a mix.** Forgejo's wiki has no notion of a page's language (no switcher, no
fallback to English), so nothing is built out of page names inside it.

- **While you use Postulo**, the translated text is the in-app help (#302), written as
  templates and translated through the catalogues above. The wiki is not translated for it.
- **Before you have an instance** (*Home*, *Installing Postulo*, *Getting started*) the pages
  are a **static documentation site built from the wiki repository** by
  `scripts/wiki_site.py`. Every other page stays English, on Forgejo, and the site says so.

### What the wiki repository holds

```
Getting-started.md              the English source, as Forgejo shows it
site.json                       the site's own few words (banners, labels)
lang/<tag>/Getting-started.md   a translation; <tag> is a canonical BCP 47 tag (pt-PT, fr-FR)
lang/<tag>/site.json            the same words in that language; a missing key falls back
lang/stamps.json                which English each translation was read against
```

### The tool

```sh
uv run python scripts/wiki_site.py build ../postulo.wiki site/      # the whole site
uv run python scripts/wiki_site.py stale ../postulo.wiki --strict   # fail if a source moved
uv run python scripts/wiki_site.py stamp ../postulo.wiki fr-FR      # fr-FR has been read
uv run python scripts/wiki_site.py components ../postulo.wiki       # what Weblate is set up with
```

- **Landing.** `index.html` lists the languages, each in its own name with `lang` set; each
  language has the same pages, with links that stay inside it. A page with no translation is
  the English page with a banner in the reader's language saying so, never a 404. Which
  language a visitor is sent to first is the web server's job (`Accept-Language` on whatever
  serves `site/`); the landing is what works with nothing.
- **Staleness.** A stamp is a hash of the English file's text, so it needs no git history and
  does not care what a translation platform does to the translated file. `build` puts a
  banner on a translation whose English has moved, or that was never stamped; `stale` lists
  them and `--strict` fails, so CI on the wiki can refuse a source change that leaves a
  translation describing last year's interface unless it is marked. `stamp` is what a
  reviewer runs after reading a translation against today's English: it is the page's
  equivalent of deleting `draft` from a string.
- **Weblate.** `components` prints one component per page plus one for `site.json`, in the
  file mask, template and BCP language-code style Weblate needs (`lang/*/Page.md`,
  with `pt-PT` written as `pt-PT`, not `pt_PT`). The component reads and commits the wiki
  repository the way the catalogues' component does (#349). The components have not been created on
  the live Weblate yet: check the `file_format` name in the output against the version the
  instance runs when doing so, which the script cannot do. A translation arriving from
  Weblate is not read by a speaker yet: it is *unstamped*, which the site shows as a banner
  until somebody stamps it.
- **Cost.** The three pages are about six thousand words, so a language costs a machine
  draft and one review of that size, not the 77,000 of the whole wiki. Do *Getting started*
  first.
- **Images** are shared by every language, so a screenshot with words in it stays English
  until the generated screenshots (#353) can be made per language.
- **Commitments.** The output is HTML, one stylesheet and the wiki's images: no script and
  nothing from a third party, both colour schemes, `dir="rtl"` for the languages `RTL` lists.
  Raw HTML in a page is cut down to a picture, a link and a few inline tags, because a
  translation comes from a platform others can write to.

## Plugins

A plugin's strings are the plugin's to translate: its `locale/` directory sits beside the
package and Postulo reads it when the plugin loads. See [Writing a plugin](https://source.tiagoagueda.com/postulo/postulo/wiki/Writing-a-plugin).

That is true of the plugins Postulo ships as well. Their catalogues live beside their
packages — `src/postulo/plugins/builtin/locale/` for the two built-in capture sources —
and are edited, checked and reviewed exactly like the ones in `src/postulo/locale/`. The
completeness rule for the European Union languages applies to each set separately, so a
built-in cannot quietly fall behind.

Postulo's own catalogue is read before any plugin's, so where both translate the same
English string, Postulo's rendering is the one shown.
