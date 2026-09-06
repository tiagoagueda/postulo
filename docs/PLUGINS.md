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
`postulo.notifications.email.EmailNotifier` is forty lines and a fair template;
[postulo-apprise](https://source.tiagoagueda.com/postulo/postulo-apprise) is a complete
one built outside the core, with `validate`, `summary`, a secret that is a list, and the
destination policy applied to the servers its URLs name.

### Stores

A store keeps a *copy* of a document somewhere else — an archive such as Paperless, a
share, a folder. Local media stays the source of truth: rendering, serving, export and the
review of what was sent never depend on a store, and a job search does not stop because
an archive server is down. A store adds one method:

```python
from postulo.documents.stores import DocumentMetadata, ExternalRef


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
built-in `postulo.documents.stores.LocalStore` is the same contract applied to private
media, and cannot be switched off.

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

**From the command line**, which is the same code:

```sh
manage.py plugins list
manage.py plugins install ./postulo_apprise-0.1.0-py3-none-any.whl
manage.py plugins install postulo-apprise      # by name, from a catalogue
manage.py plugins disable postulo-apprise      # stops it loading; the files stay
manage.py plugins remove postulo-apprise
manage.py plugins sync                         # what the entry point runs at boot
```

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
