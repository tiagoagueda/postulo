# Writing a plugin

Postulo is modular on purpose: anything that could reasonably vary sits behind an
interface that a separately installed package can implement, and the built-in
implementations are plugins that happen to ship in the box.

**Capture sources** were the first kind, and most of this document is about them.
**Notifiers** — how you are told that a reminder is due or a capture has arrived — are the
second, with email built in; see *Plugins that connect to another service* below for the
connection machinery they and the coming **stores** and **syncs** share.

# Writing a capture source

Postulo reads job postings through **sources**. Two are built in, and anyone can add
more by installing a Python package — no fork, no patch to this project, and no waiting
for it to be accepted.

This exists because the person who cares about a particular job board is almost never the
person maintaining Postulo. If a board matters to you, you should be able to teach one
instance about it in an afternoon.

## What a source is

Anything with four names. There is no base class to inherit, on purpose: a plugin should
not have to import Postulo internals, or track their changes, just to be recognised.

```python
class MyBoardSource:
    name = "myboard"  # recorded against every capture this source produced
    version = "1.0"  # so a capture can be traced to the code that made it

    def can_handle(self, url: str) -> bool:
        """Whether this source wants to parse the URL."""
        return "myboard.example" in url

    def parse(self, url: str, html: str) -> JobPostingData | None:
        """Extract a posting, or return None if the page yielded nothing useful."""
        raise NotImplementedError
```

Sources are given a URL and the HTML fetched from it, and return data. That is all they
do. A source does not touch the database, decide whether its own result is good enough,
or create anything — the person capturing does that on the review screen.

That boundary is the whole design. A parser reading markup it has never seen gets things
wrong, and when it does, the cost should be a few seconds of somebody's attention rather
than a fabricated job title in their records.

## The data you return

`postulo.plugins.base.JobPostingData` is a Pydantic model, and it is the only shape
Postulo accepts. The built-in parsers, your plugin and the capture API all validate
through it, which is what stops a source inventing a field or misspelling one.

| Field | Type | Notes |
| --- | --- | --- |
| `title` | `str` | The only required field |
| `company_name` | `str` | |
| `location` | `str` | Free text, as it should read on screen |
| `remote_type` | `str` | `onsite`, `hybrid` or `remote` |
| `employment_type` | `str` | `full_time`, `part_time`, `contract`, `freelance`, `internship`, `apprenticeship` |
| `description` | `str` | Plain text. Truncated past 40 000 characters rather than rejected |
| `salary_min` / `salary_max` | `Decimal \| None` | |
| `salary_currency` | `str` | Three letters |
| `salary_period` | `str` | `year`, `month`, `day` or `hour` |
| `posted_at` / `closes_at` | `date \| None` | |
| `url` | `str` | |
| `source` | `str` | Where it came from, usually the hostname |

Unknown fields are rejected outright. If you need one Postulo does not have, open an
issue — an extra column that only one plugin understands helps nobody.

**Leave a field empty rather than guessing at it.** A blank box on the review screen is
an invitation to type; a confidently wrong value is something a person has to notice
before they can correct it, and they will not always notice.

## Registering it

Advertise an entry point in the group `postulo.sources`:

```toml
# pyproject.toml of your plugin package
[project.entry-points."postulo.sources"]
myboard = "my_package.source:MyBoardSource"
```

Install the package into the same environment as Postulo:

```sh
uv pip install my-postulo-myboard
```

That is the whole installation. Restart Postulo and the source appears on the capture
page. Uninstalling the package removes it.

## Translations: every plugin holds its own

Postulo speaks many languages, and a plugin must speak them itself. Its labels, help
texts and messages are never added to Postulo's catalogues: a plugin author adds a
language without waiting for a Postulo release, and a plugin translated into a language
Postulo does not yet have still shows it. The rule is one directory:

```text
my_package/
    __init__.py
    source.py
    locale/
        fr_FR/LC_MESSAGES/django.po
        fr_FR/LC_MESSAGES/django.mo
        pt_PT/LC_MESSAGES/django.po
        pt_PT/LC_MESSAGES/django.mo
```

Wrap every string a person will read in `gettext` or `gettext_lazy`, exactly as Postulo
does, then from the package's directory:

```sh
django-admin makemessages --locale fr_FR --locale pt_PT   # writes locale/*/django.po
django-admin compilemessages                              # writes the .mo files
```

Ship the compiled `.mo` files in the package. When the registry loads a plugin it adds the
package's `locale/` to the directories Django reads catalogues from, so nothing else is
needed; a plugin without a `locale/` simply shows its English. The languages worth
covering first are the ones Postulo itself ships (see `docs/TRANSLATING.md`), and a
catalogue that is only partly translated is better than none.

**Postulo's own catalogue is read first, and that decides a tie.** Django merges the
locale directories in reverse order with each merge overriding the last, so the first
wins, and a plugin's is always appended. If your plugin translates a string Postulo also
uses — `Name`, `Send`, `Draft` — the reader sees Postulo's rendering of it, not yours. A
plugin can add a word to the interface; it cannot change one.

**A plugin Postulo ships follows the same rule** since #127, which it did not before: its
catalogues sit beside its package and every check Postulo runs over its own catalogues
runs over the plugin's too. `scripts/messages.py` finds them from the filesystem — a
directory under `src/postulo` with a `locale/` in it is a set of catalogues — so moving a
built-in's strings out of core is a matter of creating the directory and re-running
`extract`, with nothing to add to a list. The twenty-four European Union languages have to
stay complete in *every* set, which is the guarantee that would otherwise have been traded
for a tidier layout: for a plugin Postulo ships, Postulo is the author, and a string that
leaves the completeness test's sight is not translated by somebody else — it is quietly
untranslated.

## How sources are chosen

Third-party sources are tried first, in the order the entry points resolve, then the
built-in ones. A source written for a specific site knows more about it than a general
parser does, so it gets first refusal.

For each source in turn, Postulo calls `can_handle(url)` and then `parse(url, html)`,
and takes the first result that is not `None`.

A source that **raises** is logged and skipped — the next one along may well cope, and a
broken plugin should not take capture down with it. A source that does not provide the
four names is refused at load time and logged. Neither failure reaches the person
capturing, who simply gets a result from something else.

## The built-in sources

**`schema.org`** reads the `JobPosting` object most large boards already embed as
JSON-LD for search engines. It is a published standard which the sites maintain
themselves, so reading it breaks far less often than guessing at their markup: there are
no CSS selectors to repair when a board redesigns. Try this before writing anything —
your board may already be covered.

**`page-metadata`** is the fallback. It takes the title the page declares and its
readable text, and lets the person capturing fix the rest. Deliberately unambitious.

## Testing yours

Give it HTML and check what comes back. Do not write tests that fetch a live site: they
fail for reasons that have nothing to do with your code, and usually at the worst moment.

```python
def test_it_reads_a_posting():
    data = MyBoardSource().parse("https://myboard.example/j/1", SAVED_PAGE_HTML)

    assert data.title == "Senior Backend Engineer"
    assert data.employment_type == "full_time"
```

Keep a saved copy of a real page as a fixture, and refresh it when the board changes.

## Fetching, and what your source does not have to think about

Postulo has already fetched the page by the time your `parse` is called, and it did so
under rules your source inherits for free:

- only `http` and `https`;
- every address the hostname resolves to must be publicly routable, revalidated on each
  redirect;
- the site's `robots.txt` is honoured;
- one page, ten seconds, two megabytes, three redirects.

If you find yourself wanting to fetch a second page from inside `parse` — an API the
board offers, say — think carefully. You would be making requests outside all of the
above, on an instance whose owner did not ask for them. Prefer teaching the API to
`postulo.plugins.fetching` over reaching for `httpx` yourself.

## Plugins that connect to another service

Sources are stateless. A **notifier**, a **store** or a **sync** talks to another service on
a person's behalf, and needs to know where it is and how to sign in. That is a
*connection*, and it is Postulo's business, not the plugin's: the plugin says what it
needs, Postulo draws the form under *Settings → Connections*, keeps the answers — secrets
encrypted, never shown back — and hands them over when it calls the plugin.

A connected plugin provides four names and two methods, no base class:

```python
from postulo.plugins.base import FieldSpec, TestResult
from postulo.plugins import http


class MyNotifier:
    name = "mynotifier"  # stable identifier, recorded on every connection
    version = "1.0"
    kind = "notifier"  # or "store", or "sync"
    label = "My notifier"  # what people see

    def config_fields(self):
        return [
            FieldSpec("url", "Server address", type="url"),
            FieldSpec("token", "API token", type="password", secret=True),
            FieldSpec("quiet", "Quiet hours", type="boolean", required=False),
        ]

    def test(self, config):
        """One real request, one sentence back. Runs when a person presses Test."""
        with http.client() as client:
            response = client.get(
                f"{config['url']}/ping", headers={"Authorization": config["token"]}
            )
        if response.status_code != 200:
            return TestResult(False, f"the server answered {response.status_code}")
        return TestResult(True, "reachable")
```

Field types: `text`, `url`, `email`, `password`, `integer`, `boolean`, `choice` (give
`choices`), `textarea`. A field marked `secret` is stored encrypted and is never rendered
back; `config` as passed to your methods holds configuration and secrets together.

Register it in the group for its kind — `postulo.notifiers`, `postulo.stores`,
`postulo.syncs`:

```toml
[project.entry-points."postulo.notifiers"]
mynotifier = "my_package:MyNotifier"
```

Connected plugins hold their own translations too — the `locale/` directory described
above applies to every kind of plugin.

**Use `postulo.plugins.http.client()` for every request.** It carries Postulo's timeouts and
user agent, and it enforces the instance's destination policy on every request, redirects
included: private and local addresses are refused unless the operator set
`POSTULO_CONNECTIONS_ALLOW_PRIVATE=true`, which is where self-hosted services usually live.
A plugin that opens its own connection bypasses that policy, and a reviewer will say so.

Two more methods are optional, and Postulo looks for them by name:

```python
def validate(self, config) -> dict[str, list[str]]:
    """Runs when the form is submitted, with configuration and secrets together.

    Return problems keyed by field name; an empty key is a problem with the form
    as a whole. An empty dict means the configuration is fine.
    """
    if "://" not in config["url"]:
        return {"url": ["That is not an address."]}
    return {}


def summary(self, config) -> str:
    """One line for the connections list. Mask every secret part."""
    return f"{config['url']} as {config['token'][:2]}…"
```

`validate` is where a plugin that can tell a typo from a token says so — at the form,
rather than at three in the morning when a reminder falls due. `summary` exists because
secrets are never shown back: it is how a person tells two connections to the same plugin
apart. A secret may be a `textarea` when it is naturally several lines (Apprise takes a
list of URLs with the credentials inside); it is stored, masked and kept-when-blank like
any other secret.

**Dependencies.** A plugin declares its own — `apprise`, `imapclient`, whatever it
speaks — in its `pyproject.toml`, and nothing else: Postulo is not on PyPI, so naming it
there sends pip looking for something it cannot find. In the container image a plugin is
installed under the core's lock as a constraint, so a plugin cannot change the version of
a package Postulo itself pins; declare the loosest bound that works.

What each kind then *does* is that kind's own interface. So far:

### Notifiers

A notifier adds one method:

```python
from postulo.notifications.base import Notification


class MyNotifier:
    ...

    def send(self, notification: Notification, config: dict, user) -> None:
        """Carry one message. Raise on failure; Postulo records it on the connection."""
```

`notification` has an `event` (`reminder_due`, `capture_received`, `went_quiet`), a
`title`, an optional `body` and an optional `url`. `user` is the person it is for — fall
back to their address or name if your service needs one. Every notifier connection
automatically carries a switch per event, so your plugin never has to ask which events to
deliver: if `send()` is called, the person wanted it. The built-in
`postulo.plugins.email` is forty lines and a fair template;
[postulo-apprise](https://source.tiagoagueda.com/postulo/postulo-apprise) is a complete
one built outside the core, with `validate`, `summary`, a secret that is a list, and the
destination policy applied to the servers its URLs name.

### Outboxes

An outbox sends mail **as the person**, from their own address, over their own server. It is
a kind of its own rather than a second transport, and the reason is worth knowing before you
write one:

| | `transport` | `outbox` |
| --- | --- | --- |
| Belongs to | the instance | the person |
| Sends as | the instance | the person |
| Configured | *Server settings → Email* | *Settings → Connections* |
| Switched off | never, while it is the way back in | by the person, or by an administrator |
| Can carry a password reset | yes | **never** |

That last row is the point. Getting back into an account reads transports, so nothing that
is not a transport can become a way in — which is what makes "the person may switch this
off" safe to offer at all.

```python
class MyOutbox:
    ...
    kind = "outbox"

    def send(self, message, config) -> int:
        """Send one Django EmailMessage. Return how many went. Raise if it would not go."""
```

**The sender is not yours to rewrite.** Postulo fills in the address the connection declares
when the message has none, and refuses a message that claims a different one — it does not
quietly replace it, because a message whose `From` was replaced is a message that looks
forged. A bounce belongs to the person, not to the instance, and it can only reach them if
their address is the one on the message.

**Raise rather than swallow.** A rejection from somebody's own mail server is theirs to read.

### Stores

A store keeps a *copy* of a document somewhere else — an archive such as Paperless, a
share, a folder. Local media stays the source of truth: rendering, serving, export and the
review of what was sent never depend on a store, and a job search does not stop because
an archive server is down. A store adds one method:

```python
from postulo.plugins.api import DocumentMetadata, ExternalRef


class MyStore:
    ...
    kind = "store"

    def put(self, document, file, metadata: DocumentMetadata, config, user) -> ExternalRef | None:
        """Keep a copy. Return where it went, or None to say "not for me". Raise on failure."""
```

`file` is open for reading; `metadata` is plain values — `kind` (a `DocumentKind`),
`kind_label`, `origin` (`render` or `upload`), `title`, `filename`, `content_type`,
`created_at`, `checksum`, `size`, `company`, `role`, `application_url`, `sent_on`,
`language`, `tags` — enough to file it sensibly without opening it. Return an
`ExternalRef(store, id, url)`; Postulo keeps it beside the document, shows the link, and
carries it in the export. Return `None` to decline a kind you do not keep (an archive for
paperwork may decline a video); the person sees *not accepted*. Raise on failure: the
scheduler retries with a growing wait and the document shows the error.

Postulo calls `put` from the scheduler, never inside a request, except when a person
presses *Send to stores now*. Every store connection carries a switch per document kind,
so your plugin never asks which kinds to keep: if `put` is called, the person wanted it.
`browse()` and `delete()` are reserved for a later stage and not called yet. The
built-in `postulo.plugins.localstore` is the same contract applied to private media, and
cannot be switched off.

### Syncs

A sync keeps records here and records elsewhere the same, in both directions — contacts
in an address book, interviews in a calendar. It adds one method:

```python
from postulo.plugins.base import SyncReport
from postulo.plugins.models import SyncLink


class MySync:
    ...
    kind = "sync"

    def sync(self, connection, config) -> SyncReport:
        """Compare both sides, push and pull what you must, say what you did."""
        report = SyncReport()
        for contact in Contact.objects.for_user(connection.owner):
            link = SyncLink.for_record(connection, contact)
            ...
            SyncLink.bind(
                connection, contact, remote_href=href, uid=uid, etag=etag, local_hash=digest
            )
            report.pushed += 1
        return report
```

What ties a local record to its remote twin is a `SyncLink` row against the connection —
the remote address, the identifier the remote uses, the version tag it last gave, a hash
of what was last pushed — kept beside the record, never on it. `SyncLink.for_record`,
`of_model` and `bind` are the whole API. When the other side deletes a twin, set
`remote_gone` on the link rather than deleting the local record: a swipe on a phone must
not erase an interview. Every sync connection carries an interval — fifteen minutes to
a day — and the scheduler runs it when that comes round; *Sync now* on the connection
runs it at once. Return a `SyncReport` with counts and `notes` for anything a person
should know; raise only when the run cannot happen at all. The report's summary and any
error are shown on the connection.
[postulo-dav](https://source.tiagoagueda.com/postulo/postulo-dav) is the reference:
CardDAV and CalDAV, both directions.

### Suggesting, instead of writing

A plugin that reads something outside Postulo — a mailbox, a calendar, a board — is
guessing, and a wrong guess in the record is worse than no guess at all. So nothing a
plugin infers is written straight into an application. It becomes a **suggestion**:

```python
from postulo.applications.suggestions import suggest

suggest(
    connection.owner,
    source="imap",  # your plugin's name
    external_id=message_id,  # what you call it; makes this idempotent
    application=found,  # or None when you cannot tell
    kind="rejection",  # a postulo.applications.models.EventKind
    summary="We are moving forward with other candidates",
    body=excerpt,
    occurred_at=when,
    suggested_status="rejected",  # optional: a Status to move it to
    proposed_dates=["12/09/2026 14:00"],  # optional: dates a message offered, as written
    context={"From": sender},  # anything the person should see
)
```

It lands under **Applications → Suggestions**, and the person accepts or declines it.
Accepting writes it through `record_event` or `change_status` with your plugin's name as
the actor, so the timeline shows what an automatism did and the person can undo it by
hand. **Given an `external_id`, `suggest` is idempotent for that source and person** —
a second call finds the first suggestion and changes nothing, whether it is waiting,
accepted or declined. That is what lets a mailbox be read every five minutes without
asking the same question twice.

## What you may import from Postulo

One module, and it is a promise:

```python
from postulo.plugins.api import FieldSpec, TestResult, declares, shipped
```

**Everything else in `postulo` is this month's internals.** `postulo.plugins.base`,
`postulo.core`, `postulo.accounts` — whatever they look like today, they may move in a patch
release without a word. `postulo.plugins.api` is the only thing that will not.

**Total independence is not the goal and cannot be.** Four things are reasons to depend on
Postulo, and skipping any of them breaks something the project promises rather than merely
being untidy:

| You need | Because |
| --- | --- |
| `OwnedModel`, `OwnedQuerySet` | a plugin holding one person's data scopes it with `for_user()`, or one person sees another's |
| `safe_next` | a redirect that skips it is a way to bounce somebody off the instance |
| `client` | an outbound request that skips it is a way to make the server dial where it should not |
| `access_token` | ask for a token at the moment of use; a plugin that keeps one has stopped refreshing it |
| `ACCESS_TOKEN` | where a consent plugin whose method is handed *settings* rather than a connection finds the token Postulo renewed for it just before a send or a test (#151) |

The rest of the surface is what you declare and what you hand back: `Manifest`, `declares`,
`shipped`, `manifest_of`, `label_of`, `description_of`; `FieldSpec`, `TestResult`, `Consent`;
`JobPostingData`, `SyncReport`, `TextMessage`; `MAX_IMPORT_BYTES`, `ImportRefused`,
`refuse_unreadable`; `MAIL`, `TEXT`, `MEDIUMS`, `medium_of`; and every protocol.

**`needs_consent` may take the connection's settings.** A plugin that always authenticates
by consent declares `needs_consent(self)` and returns a `Consent`. One that sometimes does —
*Your own email* signs in with a password for a server of one's own and by consent for Google
or Microsoft 365 — declares `needs_consent(self, config)` and returns `None` when the settings
say a password. Postulo passes the settings to whichever form the method takes (#151).

Asking for anything else raises, and says where to look:

```
AttributeError: 'Connection' is not part of the plugin surface. See docs/PLUGINS.md.
```

### What the promise is worth

These names keep working across a minor release. A change to one is a `### ⚠️ Deprecated`
entry first and a `### 🗑️ Removed` entry in a later release, never a silent rename. That is
the cost of having a surface at all, taken deliberately: without one, #129 would move every
shipped plugin into its own package against a boundary nobody had written down, which is how
a surface gets set by accident instead of on purpose.

### What is not settled yet

What a dashboard widget is handed is an open question. A plugin needing it is reaching past
the surface knowingly — and `tests/test_plugin_surface.py` records every shipped plugin that
does, with the reason. That list is the map of what is left to move, and a new entry has to
be added on purpose, because the test fails until somebody writes down why.

What a *document* template may be is settled: `Theme` and `ThemeKind` are on the surface, and
**A theme, if you set documents** below is the whole of it.

Today the two built-in **sources** import nothing from Postulo at all, and the
telephone-numbers **feature** imports only the surface. That is the check that the surface is
not so wide as to be meaningless.

## The plugins Postulo ships are packages like yours

Eleven of them across eight kinds, and they live where yours would:

```text
src/postulo/plugins/
    builtin/          schema.org and page-metadata, the two capture sources
    email/            the notifier that sends through the instance's mail settings
    smtp/             the transport underneath it
    own_mail/         the outbox that sends as the person rather than as the instance
    localstore/       the store every document is in
    europass/         the importer, and the reader it is a declaration for
    phone_numbers/    the feature that switches several numbers per person on and off
    email_addresses/  the feature that governs a page and owns no data at all
    postal_rules/     what each country expects of an address, and what it calls each part
    identifiers/      which external identifiers exist, and what each one identifies
```

Each has its manifest, its `locale/`, and imports `postulo.plugins.api`. None of them
touches the database: an importer turns bytes into a record, a store writes a file, a
notifier sends a message, and Postulo does the ownership scoping. That is not tidiness —
scoping done wrong in a plugin is how one person sees another's data, and the way not to
get it wrong in seven places is not to need it in seven places.

**This is checked, not asserted.** `tests/test_plugin_surface.py` takes the list from the
registry rather than from a list in the test, so a built-in added tomorrow is checked
tomorrow. It fails on one that lives outside `postulo.plugins`, one with no catalogues of
its own, one that imports a model, and one that imports anything else from Postulo without
a written reason.

**What a built-in does differently, and why.** It registers through `register_builtin()`
rather than an entry point. An entry point would buy nothing — the module is in the same
distribution, so the lookup is a slower import — and would cost the ordering `docs` promises
just above: third-party plugins are tried first, and built-ins last, so yours can take
precedence over Postulo's. Entry points resolve in one list with no way to say *after
everything else*.

## A logo, if you have one

Name a file inside your package and Postulo serves it:

```python
@declares(
    Manifest(
        name="paperless",
        label="Paperless",
        ...
        logo="paperless.png",
    )
)
class PaperlessStore:
    ...
```

A **name**, never a path: a separator or a leading dot is refused rather than normalised,
and the file is read out of the package with `importlib.resources`, so nothing you declare
can name a file outside it.

**Raster only** — PNG, JPEG, GIF, WebP. SVG is the format logos usually arrive in and the
one that needs care, because it can carry scripts and references to other files and a direct
visit to the file is not the `<img>` context where a browser refuses to run them. Postulo
decodes what you shipped and writes it out again as a 256-pixel PNG, so what is served is an
image Postulo produced rather than your file passed through. Under 2 MB.

**Postulo serves it; you do not.** There is no way to give a URL, and that is the point
rather than an omission: an `<img>` at your server would tell you which instances run your
plugin, how many people use it, and when. The production policy is `img-src 'self'` for
exactly that reason.

**Having no logo is the normal case.** The interface falls back to the initials tile it
already uses for a person with no picture and a company with no logo, so nothing is ever a
broken image — and a declared file that is missing, too large, or not an image falls back the
same way, with a line in the log rather than a broken page.

### If the logo is somebody else's mark

**Do not put it in Postulo, and Postulo does not put one in itself.** Displaying a mark to
say "this reads Europass files" is nominative use and is what every integration directory
does. *Shipping the file* is a different statement: Postulo is AGPL-3.0, and that licence
grants rights to the code — it cannot sublicense a mark the project does not own, so every
fork would be redistributing somebody's trademark under a licence with nothing to say about
it. The Commission's own reuse decision, which makes its documents freely reusable,
**excludes logos and trademarks from its scope**, so "it is an EU document" is not an answer
either.

So none of the plugins Postulo ships carries a logo. The Europass importer says "Europass",
and its name does the identifying. A plugin distributed by whoever owns the mark is a
different situation and theirs to decide — this rule is about what *this repository*
redistributes.

The rule has to be the same for every mark, which is why it is written here rather than
decided per logo: a rule that only works for the logos a project happens to like is not a
rule. Where a mark's owner permits redistribution, the shape to copy is
`src/postulo/static/flags/LICENSE.txt` — the notice travels with the files.

## A theme, if you set documents

A **theme** is a way of setting a CV or a letter: a template per kind, and the CSS inlined
in it. Declare one and Postulo offers it in the picker beside its own.

```python
from postulo.plugins.api import Theme, ThemeKind, Manifest, declares


@declares(Manifest(name="vellum", kind="feature", label="Vellum"))
class Vellum:
    themes = [
        Theme(
            name="vellum",
            label=_("Vellum"),
            templates={ThemeKind.LETTER: "vellum/letter.html"},
            provider="Vellum Press",
        )
    ]
```

Put the templates in a `templates/` directory beside your package, exactly as you put your
catalogues in `locale/`. Registering the plugin puts that directory on the path Django looks
for templates in; nothing is copied and nothing is compiled.

```text
vellum/
    __init__.py
    locale/
    templates/
        vellum/
            letter.html
```

**`templates` is the declaration.** A theme sets what it has a template for, and nothing
else. If yours sets letters but not CVs, say so by having only the one file: the CV picker
will not offer it, and asking for the pair directly raises `CannotRender` with a sentence in
it rather than a missing-template traceback. A theme that answered for a kind it had never
seen by falling back to Postulo's `plain` would put somebody's Vellum letter beside a plain
CV in one envelope, which is the same bad outcome said too late to do anything about.

**Your template owes the reader a language and a direction.** Postulo hands you
`document_language` and `document_direction`; a template that ignores them renders an Arabic
CV left to right. The simplest way to get this right is to extend Postulo's own base:

```django
{% extends "documents/themes/base_letter.html" %}
{% block page_styles %}…{% endblock %}
```

which is a template, not a Python import, and is what Postulo's own two themes do.

**What you may not do is ship markup somebody else wrote at runtime.** There is no upload
form for themes and there will not be one: rendering executes the template, so an
uploadable theme is remote code execution with a file picker on it. Your templates run
because an administrator installed *your plugin*, having read its author, licence and
source — the same trust that already lets it hold their credentials, not a new one.

**Names.** Postulo's own — `plain` and `classic` — are not yours to take, and the first
plugin to claim any other name keeps it; a second is logged and ignored. Sixty characters is
the limit, because a column has to hold it.

**`provider` is shown to the person choosing.** The picker reads "Vellum, from Vellum Press"
where you give one, and just the label where you do not. Postulo's own carry none, because
there is nothing to distinguish them from — but a theme of yours is markup that runs when
somebody exports a document, and whose it is belongs beside the name rather than in a page
they would have to go looking for.

## Saying who you are

Everything a plugin says about itself goes in one place: a **manifest**.

```python
from postulo.plugins.base import Manifest, declares


@declares(
    Manifest(
        name="acme-board",
        label="ACME Board",
        version="1.4.0",
        kind="source",
        description="Reads postings from ACME's board.",
        author="First Last <first.last@example.org>",
        licence="AGPL-3.0-or-later",
        source_url="https://example.org/your-plugin",
        logo="https://example.org/your-plugin/logo.png",
    )
)
class AcmeBoardSource:
    def can_handle(self, url): ...
    def parse(self, url, html): ...
```

`@declares` attaches the manifest **and** sets `name`, `version`, `kind`, `label` and
`description` on the class from it, so the protocol members the registry reads and the
manifest cannot disagree. You may write them by hand instead; the manifest is optional and
so is the decorator.

`name` is the one field that is not free to change later. It is what the registry keys on
and, for a source, the value written into every capture's `source` field — renaming it
orphans the history of every capture anybody made with your plugin.

`logo` names an image to show beside your plugin's name. Nothing renders it yet.

**A manifest is optional, and so is every field but `name`.** A plugin that declares nothing
still loads, and shows its identifier where a name would be. That is not politeness: the
protocols are `runtime_checkable`, which means they check data members as well as methods,
and `registry.py` drops anything that fails `isinstance` — so making any of this required
would silently unload every plugin written before it existed. One optional attribute
carrying any number of facts is what makes the set extensible at all.

**Your packaging is the fallback.** Postulo reads your wheel's metadata at install time and
keeps it, and `base.manifest_of` fills in anything your manifest left out from there. So a
plugin that declares nothing but packages itself properly still shows an author and a
licence on *Server settings → Plugins*:

```toml
[project]
description = "One sentence about what this does."
license = "AGPL-3.0-or-later"
authors = [{ name = "First Last", email = "first.last@example.org" }]

[project.urls]
Source = "https://example.org/your-plugin"
```

Both halves of `authors` matter: **with an email** it becomes `Author-email: First Last
<address>`, without one it is just a name. The URL is read from `Project-URL` — `Source`
first, then `Repository`, then `Homepage` — because `Home-page` is setuptools' old field and
modern backends do not write it.

Read it all back with `base.manifest_of(plugin)`, which is the one place anything in Postulo
asks who a plugin is. `base.label_of` and `base.description_of` are shorthands over it.

**The plugins Postulo ships use the same machinery**, through a `shipped()` helper that fills
in the author, the licence, the source URL and Postulo's own version — because a built-in
claiming an independent version number is inventing a fact, and one that ships with the
application changes when the application does. There are six of them, and a test walks every
one and fails on a missing field, so this is not a rule the project asks of you and not of
itself.

## Identifier registries

An **identifier** plugin says which external identifiers exist — ORCID, ISNI, Wikidata, a
legal-entity identifier, a national company register number — what each should look like,
where it links, whether its check digits add up, and **which subjects it identifies**:

```python
Scheme(ORCID, "ORCID", pattern=..., subjects={"person"})
Scheme(LEI, "LEI", pattern=..., subjects={"company"})
Scheme(ISNI, "ISNI", pattern=..., subjects={"person", "company"})
```

That last field is the whole point of the kind. Two registries kept two sets of choices, and
the sets were not disjoint: ISNI identifies contributors *and* organisations by its own
definition, Wikidata has items for both, LinkedIn has profiles and company pages. Keeping
them apart had quietly picked a side for each, so a researcher could not record their
Wikidata item and a university could not record its ISNI (#109).

**A scheme owns no rows**, which is why this can be a plugin at all: `PersonIdentifier` and
`CompanyIdentifier` are core models, migrated by core, and a plugin contributes only the
vocabulary. A plugin that wanted to own a table could not, and that is a real limit rather
than an oversight.

**It cannot be switched off.** Every other kind answers *is this on for this person*; a
registry answers *what does this key mean*. Off would leave every stored identifier without
a label, a link or a check, which is not what *off* means anywhere else here — so
`identifier` is ungoverned, alongside transports.

**It cannot reach the network**, and neither may yours. A scheme validates what somebody
typed and knows where it links; a checksum catches the typos a lookup would, which is the
entire reason ORCID and the LEI have one.

**No third-party group is advertised yet**, and the kind is internal for now. That is not
shyness about the idea — `register` is one generic scheme for SIRET, NIF, Companies House,
KvK and Handelsregister alike, and a plugin per country is the obvious next thing — but a
third-party contract is a promise about breakage, and this one is not written.

## Importers

An **importer** reads a person's career out of a file they upload — the mirror of a source,
for a different input and a different output. Europass ships as one; nothing else does yet,
and the contract for third-party importers is deliberately not written (#105), so treat this
section as a description of what exists rather than an invitation.

```python
class MyImporter:
    name = "json-resume"
    version = "1.0"
    kind = "importer"
    label = "JSON Resume"
    description = _("The jsonresume.org format.")

    def can_handle(self, data: bytes, filename: str = "") -> bool: ...
    def read(self, data: bytes): ...
```

Two rules matter more here than anywhere else in this document.

**Postulo refuses the file before you see it.** `plugins.base.refuse_unreadable` rejects an
empty upload, anything over `MAX_IMPORT_BYTES`, and a `DOCTYPE` in anything that looks like
XML — which is where entity expansion lives. That belongs to the kind rather than to each
importer, because an importer is handed a file a stranger's browser uploaded, and "every
plugin author remembers" is not a control. Raise `ImportRefused` for anything else you will
not read; the message goes to the person who chose the file.

**An importer writes nothing.** It turns bytes into a record and stops. What reaches the
database is decided on the review screen, by the person, which is the same rule a source
obeys and matters more here: a source that guesses wrong costs somebody a few seconds, and
an import writes a career.

`can_handle` answers *what is this*, not *is it any good*. A truncated Europass export is
still a Europass export, and saying "not readable XML" helps far more than "nothing here
reads that".

## Transports

A **transport** carries a message off this machine. Postulo ships one, SMTP, and names no
vendor.

The reason the kind exists: a self-hoster whose provider blocks outbound 25, 465 and 587 —
which is most residential connections and several hosts — has no route today except finding a
relay that speaks SMTP. A transport lets them install one that speaks an HTTP API instead.

```toml
[project.entry-points."postulo.transports"]
carrier = "postulo_carrier:CarrierTransport"
```

```python
class CarrierTransport:
    name = "carrier"
    version = "1.0"
    kind = "transport"
    label = "Carrier"
    description = _("Delivers over Carrier's HTTP API.")

    def config_fields(self) -> list[FieldSpec]: ...
    def test(self, config: dict) -> TestResult: ...
    def deliver(self, messages: list, config: dict) -> int: ...
```

`deliver` takes Django `EmailMessage` objects and returns how many went, which is the
contract an email backend already has — so a transport can be a thin wrapper around one where
that is the honest implementation. Raising is a failure, and the caller reports it.

**A transport is not a notifier**, and the difference is worth keeping. A notifier decides
that something is worth telling somebody and writes the words; a transport gets those words
to them. One sits on the other. Merging them would make "when should Postulo tell me things"
and "how does this instance reach the outside world" the same form, and they are not: the
first is a person's preference, the second is the operator's plumbing.

**Three things follow from that.**

*Installing yours does not redirect the mail.* The registry prefers third-party plugins for
sources — a plugin written for one job board knows more about it than a general parser does —
and that argument does not transfer to where an instance's mail goes. An administrator
chooses, on *Server settings → Email*, and until they do the built-in one carries it.

*Your settings are drawn from `config_fields()`*, on that page, exactly as a connection's
are, with `secret=True` fields encrypted at rest and never shown back. SMTP is the one
exception: its settings are named columns that predate the kind, each overridden individually
by its own environment variable, and a fresh instance has to be able to send a verification
email before there is a row in the database to read.

*The per-person plugin policy does not apply.* None of *available*, *unavailable*, *forced
on* or *forced off* means anything about mail delivery, and *forced off* would be an account
nobody can recover. Postulo refuses all four for a transport rather than merely leaving them
off the page.

**The lock.** A transport may not be switched off — nor its package removed — while it is the
last way anybody could get back into their account. That is evaluated, not hardcoded to a
name: `recovery_routes()` lists the ways in that exist, and while removing yours would empty
that list, the refusal stands and says whose accounts it is protecting. Add another route and
the lock opens by itself.

## Features

A **feature** is a capability of Postulo itself, switched on and off through the same page,
the same policy and the same explanation as everything else here. It is the only kind that
does not talk to anything outside the application.

Postulo ships two. `phone-numbers` governs whether a person and their contacts may hold more
than one telephone number; `email-addresses` governs whether the page for managing several
email addresses is offered.

**A feature governs what Postulo offers and uses. It does not have to own the data.**
`email-addresses` owns none at all — the addresses belong to allauth, with allauth's flows
reading them, and a plugin that cannot verify an address cannot honestly own one. Switching
it off offers the primary address alone and stops offering the page; it deletes nothing,
exactly as switching `phone-numbers` off leaves every number where it was.

**A feature that could strand somebody needs a floor, and the floor goes below the policy.**
Hiding the addresses page from somebody who keeps a spare because their work address is about
to stop working takes away their way back in on the day they need it — so it is never hidden
from an account that already has more than one address, whatever the policy says. If
switching your feature off could cost somebody something they cannot get back, that is the
shape to copy.

```toml
[project.entry-points."postulo.features"]
my-feature = "my_package:MyFeature"
```

```python
from postulo.plugins.base import Manifest, declares


@declares(Manifest(name="my-feature", label="My feature", kind="feature", version="1.0"))
class MyFeature:
    """A declaration. There is nothing to implement."""
```

**A feature has no methods**, and that is deliberate. It answers one question — *is this on
for this person* — and the application asks `policy.decide(name, person).on` before offering
the part of itself the feature covers. The moment a feature could *act*, "off" would mean two
different things depending on which plugin you asked.

**Off never deletes anything.** This is the promise the whole plugin system makes, stated on
two pages of the interface in every language Postulo speaks, and the kind whose subject is
Postulo's own tables is not the exception to it. What a feature governs is what Postulo
*offers* and *uses*. The rows stay where they are, the person is told how many are being kept
back, an export carries all of them regardless, and switching it on again finds them
unchanged. A feature that deletes on the way out is not a feature, it is a migration with a
checkbox in front of it.

## Getting a plugin into an instance

**From the interface.** *Server settings → Plugins* installs a wheel an administrator
uploads, or a plugin named in a configured catalogue. Plugins land in
`POSTULO_PLUGINS_DIR` — on the data volume, not in the environment — which is added to the
import path at startup, with a `plugins.json` beside them recording what is installed and
where it came from. Because the record is on the volume, an upgrade cannot lose them: the
container's entry point runs `manage.py plugins sync` at boot and reinstalls what the
record lists and the new environment lacks.

Three things are refused, each with the reason: a wheel that is not `py3-none-any` (the
image has no compiler), a package with no `postulo.*` entry point (installing it would do
nothing), and a dependency that would move one of Postulo's own — every install runs with
the running environment as a constraint.

**What is verified, and where that stops.** A catalogue's index carries an Ed25519
signature checked against the configured key, and each release names a SHA-256 that the
downloaded wheel must match. That covers the plugin's own file. It does not cover its
**requirements**: those are resolved from PyPI when the plugin is installed, and are
whatever is served that day, along with whatever they need in turn. Trusting a plugin
therefore means trusting its dependency list, and it is worth reading before you install.

Two things narrow it:

- **Only built wheels are installed** (`--only-binary :all:`). A source distribution runs
  its own build code during installation, as the container's user, before anybody has seen
  it. A plugin that genuinely needs a source build is one to install by hand, deliberately.
- **Everything that arrived is recorded** in `plugins.json` and listed under the plugin on
  *Server settings → Plugins*, so an administrator can see what is actually in the instance
  without a shell — including packages no wheel's metadata mentions, because a requirement
  of a requirement never appears there.

A catalogue carrying the whole resolved set with hashes, installed with `--require-hashes`,
is the real answer and a change to the catalogue format; it is not built yet.

**From the command line**, which is the same code:

```sh
manage.py plugins list
manage.py plugins install ./postulo_apprise-0.1.0-py3-none-any.whl
manage.py plugins install postulo-apprise      # by name, from a catalogue
manage.py plugins disable postulo-apprise      # stops it loading; the files stay
manage.py plugins remove postulo-apprise
manage.py plugins sync                         # what the entry point runs at boot
```

## Where a plugin says it came from

*Server settings → Plugins* labels every plugin with its provenance, and the label describes
evidence rather than intent:

| Label | What it means | Removable |
| --- | --- | --- |
| **Internal** | Ships inside Postulo | No — switched off for people through the policy rows |
| **Official** | Its file matches what the official repository signed | Yes |
| **Custom** | Its file matches what a repository on this instance signed | Yes |
| **Uploaded** | Nothing here signed this file | Yes |

**An upload can be official, and a name cannot make it so.** A zip carries no evidence of who
published it — so what is checked is the file. A signed index publishes each release's
SHA-256; a wheel whose bytes match one *is* the file that repository published, whatever route
it took. One byte of difference and it is not. If no repository can be reached at the moment
the question is asked, the answer is **Uploaded**, never a guess.

**Which repository is official is a key, not a name.** Anybody can call a repository
`postulo`; nobody else can sign with Postulo's key. Postulo publishes no catalogue yet, so
nothing is official on any instance today.

**None of this means safe.** Installing a plugin runs somebody else's code inside Postulo,
and that is as true of an official plugin as of any other. It also says nothing about
dependencies: the signature and the checksum cover the plugin's own wheel, and its
requirements are resolved from PyPI at install time and are whatever was served that day.

## Publishing to a catalogue

A catalogue is one JSON file listing plugins, and beside it a detached Ed25519 signature
over exactly those bytes. An administrator configures it as
`POSTULO_PLUGIN_CATALOGUES=name|url|public-key`; without the key there is no catalogue,
because an unsigned list of URLs to run code from is not something Postulo will offer. The
index is fetched when somebody presses *Check for updates*, never on its own.

```json
{
  "plugins": [
    {
      "name": "postulo-apprise",
      "summary": "Notifications through Apprise",
      "maintainer": "Tiago Agueda",
      "licence": "AGPL-3.0-or-later",
      "repository": "https://source.tiagoagueda.com/postulo/postulo-apprise",
      "releases": [
        {
          "version": "0.1.0",
          "url": "https://…/postulo_apprise-0.1.0-py3-none-any.whl",
          "sha256": "…",
          "requires_postulo": ">=0.2",
          "provides": ["postulo.notifiers:apprise"]
        }
      ]
    }
  ]
}
```

Newest release first. Every wheel is checked against the SHA-256 the *signed* index
carries, so a mirror or a hijacked download host cannot ship code. Being listed means the
people who publish that catalogue looked at the plugin — its contract, its licence, that
it does nothing with the network or with secrets beyond what it says. That is a review,
not a guarantee, and the page says so.

## Getting a plugin into the container image

The image installs a locked environment at build time and runs as a user that cannot
write to it. Installing through the interface handles that by putting plugins on the
volume; to bake one into the image instead:

```sh
# 1. A build argument: any number of packages, in the form pip accepts.
docker compose -f docker/compose.yml build \
  --build-arg POSTULO_EXTRA_PACKAGES="git+https://source.tiagoagueda.com/postulo/postulo-apprise.git"
```

```dockerfile
# 2. Your own image on top of Postulo's.
FROM source.tiagoagueda.com/postulo/postulo:0.2
USER root
RUN uv pip install --no-cache postulo-apprise
USER postulo
```

Both install into Postulo's environment with the core's lock as a constraint, so a plugin
cannot change the version of a package Postulo pins. Installing from the interface — an
uploaded package, a catalogue — is a later step on the roadmap.

## Adding a settings section

The Settings area is a sidebar of sections, each its own page. A plugin with per-person
settings registers a section and it appears beside the built-in ones, in the order it asks
for. Register it when your app is ready:

```python
from django.utils.translation import gettext_lazy as _

from postulo.core.settings_sections import SettingsSection, register

register(
    SettingsSection(
        slug="myboard",
        label=_("MyBoard"),
        url_name="myboard:settings",  # your own view, rendered with settings/base.html
        icon="link",  # any icon in assets/icons.txt
        order=60,  # built-in sections use 10 to 50
    )
)
```

Your template extends `settings/base.html` and fills `content`; the sidebar comes with it.
Give `match` the URL names of any further pages that belong to your section, so it stays
highlighted while a person is on them.

## A worked example

The complete, installable version of everything on this page is
[postulo-helloworld](https://source.tiagoagueda.com/postulo/postulo-helloworld): a
capture source and a notifier, translations of its own, tests that run both through
Postulo's registry, and a CI workflow — under the MIT licence so you can copy it into your
own plugin without a second thought. Start there. The fragment below is its source, cut
down to the shape.

```python
import json
from postulo.plugins.base import JobPostingData


class MyBoardSource:
    name = "myboard"
    version = "1.0"

    def can_handle(self, url: str) -> bool:
        return "myboard.example/jobs/" in url

    def parse(self, url: str, html: str) -> JobPostingData | None:
        # This board hides a tidy JSON blob in its page, which is far more stable than
        # its markup.
        marker = "window.__JOB__ = "
        start = html.find(marker)
        if start == -1:
            return None

        try:
            payload = json.loads(html[start + len(marker) :].split("</script>", 1)[0].strip(" ;"))
        except ValueError:
            return None

        return JobPostingData(
            title=payload.get("jobTitle", ""),
            company_name=payload.get("employer", {}).get("name", ""),
            location=payload.get("city", ""),
            description=payload.get("descriptionText", ""),
            url=url,
            source="myboard.example",
        )
```

If your source is useful to more than you, consider publishing it. Postulo does not need
to know it exists for anyone to install it.
