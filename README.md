# Postulo

[![CI](https://source.tiagoagueda.com/postulo/postulo/actions/workflows/ci.yml/badge.svg)](https://source.tiagoagueda.com/postulo/postulo/actions)

**Self-hosted job application manager, from the applicant's side of the table.**

Every applicant tracking system is built for the company doing the hiring. Postulo is
built for the person applying: your applications, your CVs, your cover letters, your
data, on your server.

From the Latin *postulō* — "I apply for". First person, deliberately.

> **Status: 0.1.0.** Usable, and used. Not yet battle-tested: it has recorded real
> applications, but by one person, for days rather than months.

## Never paywalled

**No feature of Postulo is, or ever will be, behind a paywall.** People looking for work
are, more often than not, people who cannot afford to pay for the tools to find it.
Everything this software does is available in full to everyone who runs it: no paid
tier, no "pro" edition, no licence key, no feature that unlocks later. The licence
guarantees that for the code; this paragraph guarantees it for the project's intentions.

The project's only source of income is voluntary support at
[buymeacoffee.com/tiagoagueda](https://buymeacoffee.com/tiagoagueda). Nothing is owed,
nothing is unlocked by it, and nothing in Postulo will ever ask for it.

## Secure, because of what it holds

**Postulo holds the most personal documents a person has while looking for work, and it
is built as if that were the only thing that mattered.** A CV carries a home address, a
phone number and a full employment history; a timeline says who has and has not replied.
So the code and the data are kept as secure as the project knows how to make them: every
record is owned and every query is scoped, files are never served without a permission
check, the browser is held to a strict content security policy, nothing is fetched from
the network without a deliberate decision, and the test suite includes security tests —
ownership sweeps, policy checks, the things an attacker would try — that fail the build.
Dependencies are checked for known vulnerabilities on every run and on a schedule, so a
fresh disclosure is noticed without anyone having to remember to look. A security report
is handled before any feature is; see [SECURITY.md](SECURITY.md).

## Built for everyone

**Postulo is meant to be usable by everyone, at its fullest, including people with
disabilities.** Looking for work is hard enough without the tool getting in the way. So
the interface is server-rendered HTML that works with scripts off, every control can be
reached and operated from the keyboard, images and icons carry names or are marked as
decorative, colour never carries a meaning on its own, changes on the page are announced
to screen readers, and the pages are checked against the accessibility guidelines
(WCAG 2.2, level AA) as part of the browser tests rather than as an afterthought. When a
feature cannot be made to work for someone, that is a bug, and it is filed as one.

## What it does

- **Track applications** end to end — from a posting you spotted to an offer, with an
  append-only timeline that records what actually happened and when.
- **Manage CVs** as structured, reusable career content: write an experience once, then
  compose targeted CV variants from it without copy-pasting between documents.
- **Manage cover letters** from reusable templates with per-application placeholders.
- **Keep what you actually sent.** Every document is snapshotted to PDF at send time, so
  six months later you know exactly which version that employer read.
- **Bring your own files.** Externally authored PDFs and DOCX files are stored and
  versioned alongside generated ones.
- **Capture postings** from a URL — Postulo reads the page and asks you to confirm it
  before recording anything. Extensible through plugins, and reachable through a small
  API for scripts and browser extensions.
- **See what is working** — how far applications get, what share are answered, how long
  employers take, and which sources convert. Read from the timeline, so an interview that
  ended in a rejection still counts as an interview.
- **Take everything with you** — one zip holding a readable JSON document of every record
  and every file, which imports back.

## Principles

- **Your data is yours.** Full JSON + media export, always one command away.
- **No telemetry.** No outbound calls except URL captures you explicitly trigger.
- **Private by default.** Uploaded documents are never publicly served.
- **Secure by obligation.** Ownership-scoped queries, permission-checked files, a strict
  content security policy, security tests in the suite, and dependency vulnerability
  checks in CI. See [SECURITY.md](SECURITY.md).
- **Accessible to everyone.** Keyboard-operable, screen-reader-announced, usable with
  scripts off, and checked against WCAG 2.2 AA in the browser tests.
- **Boring, durable stack.** Django, SQLite or PostgreSQL, server-rendered HTML.
- **Modular by design.** Anything that could reasonably vary — where a posting is read
  from, how you are told about things, how a PDF is produced — sits behind an interface
  that a separately installed package can implement. Postulo's own implementations are
  plugins that happen to ship in the box. See [docs/PLUGINS.md](docs/PLUGINS.md).

## Written with AI, answered for by people

Much of Postulo is written with an AI assistant, and contributions made the same way are
welcome. The rule is simple: a person proposes the change, has read it, can explain it,
and answers for it. The assistant is a tool; the contributor is accountable. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CLAUDE.md](CLAUDE.md).

## In your language

Postulo speaks every official language of the European Union. The interface is a
setting per person; the documentation is in English, and this paragraph says what
Postulo is in each language so nobody has to guess.

- **български** — Postulo е самостоятелно хостван мениджър на кандидатури за работа: вашите кандидатури, CV-та и мотивационни писма, на вашия сървър, без платени функции.
- **čeština** — Postulo je samostatně hostovaný správce žádostí o práci: vaše žádosti, CV a motivační dopisy na vašem serveru, bez placených funkcí.
- **dansk** — Postulo er en selvhostet håndtering af jobansøgninger: dine ansøgninger, CV'er og ansøgningsbreve på din egen server, uden betalte funktioner.
- **Deutsch** — Postulo ist ein selbst gehosteter Bewerbungsmanager: Ihre Bewerbungen, Lebensläufe und Anschreiben auf Ihrem eigenen Server, ohne kostenpflichtige Funktionen.
- **Ελληνικά** — Το Postulo είναι ένας αυτοφιλοξενούμενος διαχειριστής αιτήσεων εργασίας: οι αιτήσεις, τα βιογραφικά και οι συνοδευτικές επιστολές σας, στον διακομιστή σας, χωρίς επί πληρωμή λειτουργίες.
- **español** — Postulo es un gestor de candidaturas de empleo autoalojado: tus candidaturas, CV y cartas de presentación en tu propio servidor, sin funciones de pago.
- **eesti** — Postulo on ise majutatav töökandideerimiste haldur: teie kandideerimised, CV-d ja motivatsioonikirjad teie enda serveris, ilma tasuliste funktsioonideta.
- **suomi** — Postulo on itse ylläpidettävä työhakemusten hallinta: hakemuksesi, CV:si ja saatekirjeesi omalla palvelimellasi, ilman maksullisia ominaisuuksia.
- **français** — Postulo est un gestionnaire de candidatures auto-hébergé : vos candidatures, vos CV et vos lettres de motivation sur votre propre serveur, sans fonctionnalité payante.
- **Gaeilge** — Bainisteoir iarratas poist féinóstáilte is ea Postulo: d'iarratais, do CVanna agus do litreacha cumhdaigh ar do fhreastalaí féin, gan aon ghné íoctha.
- **hrvatski** — Postulo je samostalno hostani upravitelj prijava za posao: vaše prijave, životopisi i motivacijska pisma na vašem poslužitelju, bez plaćenih značajki.
- **magyar** — A Postulo saját szerveren futtatható álláspályázat-kezelő: a jelentkezéseid, önéletrajzaid és motivációs leveleid a saját szervereden, fizetős funkciók nélkül.
- **italiano** — Postulo è un gestore di candidature self-hosted: le tue candidature, i tuoi CV e le tue lettere di presentazione sul tuo server, senza funzioni a pagamento.
- **lietuvių** — Postulo — savarankiškai talpinama darbo paraiškų tvarkyklė: jūsų paraiškos, CV ir motyvaciniai laiškai jūsų serveryje, be mokamų funkcijų.
- **latviešu** — Postulo ir pašhostēts darba pieteikumu pārvaldnieks: jūsu pieteikumi, CV un motivācijas vēstules jūsu serverī, bez maksas funkcijām.
- **Malti** — Postulo huwa maniġer ta' applikazzjonijiet għax-xogħol self-hosted: l-applikazzjonijiet, is-CVs u l-ittri ta' motivazzjoni tiegħek fuq is-server tiegħek, mingħajr funzjonijiet bi ħlas.
- **Nederlands** — Postulo is een zelfgehoste sollicitatiemanager: uw sollicitaties, cv's en brieven op uw eigen server, zonder betaalde functies.
- **polski** — Postulo to samodzielnie hostowany menedżer aplikacji o pracę: twoje aplikacje, CV i listy motywacyjne na twoim serwerze, bez płatnych funkcji.
- **português** — O Postulo é um gestor de candidaturas auto-hospedado: as suas candidaturas, CV e cartas de apresentação no seu próprio servidor, sem funcionalidades pagas.
- **română** — Postulo este un manager de candidaturi găzduit pe propriul server: candidaturile, CV-urile și scrisorile tale de intenție pe serverul tău, fără funcții plătite.
- **slovenčina** — Postulo je samostatne hostovaný správca žiadostí o prácu: vaše žiadosti, CV a motivačné listy na vašom serveri, bez platených funkcií.
- **slovenščina** — Postulo je samostojno gostovan upravljalnik prijav za zaposlitev: vaše prijave, življenjepisi in motivacijska pisma na vašem strežniku, brez plačljivih funkcij.
- **svenska** — Postulo är en självhostad hanterare för jobbansökningar: dina ansökningar, CV:n och personliga brev på din egen server, utan betalfunktioner.

## Documentation

- **[The wiki](https://source.tiagoagueda.com/postulo/postulo/wiki)** — installing,
  configuring and using Postulo. Authored in [wiki/](wiki/) and published from there.
- [Implementation plan](docs/PLAN.md) — architecture, data model, and milestones
- [Writing a capture source](docs/PLUGINS.md) — the plugin contract

## Running it

```sh
git clone https://source.tiagoagueda.com/postulo/postulo.git
cd postulo
cp .env.example .env          # set POSTULO_SECRET_KEY and POSTULO_ALLOWED_HOSTS
docker compose -f docker/compose.yml up -d
docker compose -f docker/compose.yml exec postulo python manage.py createsuperuser
```

Then put a reverse proxy in front of it for TLS. See
[Installing Postulo](https://source.tiagoagueda.com/postulo/postulo/wiki/Installing-Postulo)
for the full instructions, including installing without a container.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                      # install dependencies
cp .env.example .env         # configure (a dev SECRET_KEY is generated if unset)
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver
```

PDF export uses **WeasyPrint**, which is installed with Postulo. On Linux it needs
Pango:

```sh
sudo apt install libpango-1.0-0 libpangoft2-1.0-0     # Debian and Ubuntu
```

Those libraries are awkward to obtain on Windows, so a fallback renderer exists there:

```sh
uv sync --extra chromium
uv run playwright install chromium
```

Postulo uses whichever works, preferring WeasyPrint. Export is optional: tracking
applications and writing letters need no renderer at all.

Node is **not** required to run Postulo: the compiled stylesheet is committed. It is
only needed to change the CSS, in which case:

```sh
npm install
npm run watch:css            # or `npm run build:css` for a one-off
```

To see it with data rather than an empty screen, fill an account with a fictional
search — thirty applications over six months, with documents:

```sh
uv run manage.py seed_demo you@example.org
```

Tests and linting:

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Licence

[AGPL-3.0-or-later](LICENSE). If you run a modified Postulo as a network service, your
users are entitled to its source.

The icons are [Lucide](https://lucide.dev), used under the ISC licence.

## Where this lives

Developed on [Forgejo](https://source.tiagoagueda.com/postulo/postulo) and mirrored to
GitHub. Issues and pull requests belong on the Forgejo repository; the GitHub copy is a
read-only mirror.
