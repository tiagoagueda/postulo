# Contributing to Postulo

Thank you for considering it. Postulo is developed on
[Forgejo](https://source.tiagoagueda.com/postulo/postulo); the GitHub repository is a
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
npm run build:css            # only if you touched assets/css/ or a template's classes
```

Continuous integration runs all of the above across Python 3.12, 3.13, and 3.14, plus
`manage.py check --deploy` against production settings, and it fails if the committed
stylesheet has drifted from its source. So does `uv run pytest` on your machine, once
`npm ci` has installed the Tailwind CLI: `tests/test_stylesheet.py` rebuilds it and compares,
which is the difference between finding out before the push and after it.

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

## The mark

Postulo's mark is a layered paper-cut **P**. It lives once, at `assets/brand/postulo.png`,
and everything served is derived from it:

```sh
uv run python scripts/brand.py          # write the derived images
uv run python scripts/brand.py --check  # CI runs this
```

That produces the favicons, the Apple touch icon, the two manifest icons and the header
logo into `src/postulo/static/brand/`, all committed — the same arrangement as the compiled
stylesheet and the icon set, so an instance runs without needing the tooling that made them.
Change the source, run the script, commit both; CI fails if they disagree.

The mark is **decorative everywhere it appears**. It always sits beside the instance's name,
so it carries an empty `alt` and a screen reader is not made to say "Postulo logo, Postulo".

The file in this repository is 1024 pixels square, which is twice the largest thing derived
from it. The full-resolution artwork lives wherever it is being designed; a source tree is
not an asset library.

It is a work in progress and will change. Nothing should hard-code its colours: the
stylesheet's `brand` palette is the source of truth for those.

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

## Flags

Country flags are [flag-icons](https://github.com/lipis/flag-icons) (MIT), drawn by the
`{% flag %}` tag from an ISO 3166-1 alpha-2 code:

```django
{% flag "pt" %}                        {# decorative, beside a name #}
{% flag country label="Portugal" %}    {# standing alone, so it needs a name #}
```

An **image**, not the flag emoji. A flag emoji is not a character: it is two *regional
indicator* code points — U+1F1F5 U+1F1F9 for Portugal — and a font is invited rather than
required to draw the pair as a flag. Segoe UI Emoji never has, so every Windows machine
drew `PT` (#88). This paragraph deliberately does not print the emoji to make its point,
for the same reason. Treat any emoji whose meaning depends on a font having agreed to it
the same way.

The tag renders **nothing** for a code it does not have a flag for, and every caller has to
cope with that: a language with no uncontested home is left blank rather than given
somebody's best guess. No flag beats a wrong flag.

`assets/flags.txt` lists what is in the repository, `npm run sync:flags` copies exactly
those, and `tests/test_flags.py` fails if the list and `core/phones.py` disagree — so a
country added to the telephone field is a missing flag until the script is run.

An `<option>` element can hold text and nothing else, so a `<select>` of countries cannot
show flags in its list. Where one is needed, put it beside the closed select and let each
option carry its own flag's URL in a `data-` attribute; see `partials/phone_widget.html`.
Do not replace a native select with a custom listbox to make room for pictures.

## Tables

A table wider than the card it sits in scrolls sideways, and the settings area gives its
card about 730 pixels however wide the window is — so a table that does not fit scrolls on
a desktop, not only on a phone. Measure before assuming otherwise:
`card.scrollWidth - card.clientWidth` in the browser, at 1440 as well as at 390.

Two things keep one honest:

- **Row actions are a menu**, not a row of buttons. Four buttons with their labels spelled
  out made one column of *Server settings → People* 335 pixels wide — larger than the email
  column, and holding no information at all. Use the `<details data-menu>` disclosure the
  account menu uses; the existing script already closes it when the pointer goes elsewhere.
- **`table-cards`** turns each row into a card below the `md` breakpoint, where columns have
  nowhere to go. Give every cell a `data-label` naming its column, except the one that is
  the card's heading.

`table-cards` works by making table elements blocks, and **that strips the table semantics a
screen reader navigates by** — "Administrator" stops being the Role of a row and becomes a
loose word. So a table using it must carry `role="table"`, `role="rowgroup"`, `role="row"`,
`role="columnheader"` and `role="cell"` explicitly. On a wide screen those roles are what
the elements already are and change nothing; on a narrow one they are the only thing holding
the meaning together. `server/people.html` is the worked example.

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

## Code written with AI

Code written with the help of an AI assistant is welcome on the same terms as any other
code: a person proposes it, has read it, can explain it, and answers for it. The
assistant is a tool the contributor used, not a contributor; a pull request is a
conversation between people, and the person opening it is the one who will be asked
what a line does and why. Say that a model helped, the way you would credit a library.
Review what it wrote with the care you would give a stranger's patch, especially around
ownership scoping, file handling and anything the security tests cover, because a
plausible-looking mistake is the kind that gets through. `CLAUDE.md` in the repository
root tells an assistant how this project works; keep it current when the rules change.

## House style

- **British English** in code, comments, documentation, and interface text: *organise*,
  *colour*, *licence* (noun), *behaviour*.
- **Wrap every user-facing string** in `gettext` / `gettext_lazy`, or `{% translate %}`
  in templates. Untranslatable strings are treated as bugs.
- **Never name a side of the page.** Use the logical utilities — `ms`/`me`, `ps`/`pe`,
  `start`/`end`, `text-start`/`text-end`, `border-s`/`border-e` — not `ml`, `pl`,
  `text-left` or `left-0`. They mean the same thing under `ltr` and the right thing under
  `rtl`; `tests/test_template_lint.py` fails on a physical one. Wrap text a person typed
  in `<bdi>` where it sits inline beside other text, so a Latin name inside an Arabic line
  does not throw the punctuation to the wrong end.
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
  the page with the keyboard alone before calling it done. The browser suite runs axe-core
  over every page it visits (`uv run pytest -m e2e tests/e2e/test_accessibility.py`); add
  a new page to its list.
- Keep commits focused, and describe *why* in the message rather than *what*.

## Documentation

User documentation is the [wiki](https://source.tiagoagueda.com/postulo/postulo/wiki), and
the wiki is its own git repository. Clone it beside your checkout of this one:

```sh
git clone https://source.tiagoagueda.com/postulo/postulo.wiki.git
```

Pages are written and committed there, and nowhere else: there is no copy in this repository
to keep in step (#169). A change that needs a page ships with one — a commit to the wiki that
names the same issue as the code, pushed when the code is.

- **A file name is a page name.** `Installing-Postulo.md` is the page *Installing Postulo*.
  `Home.md` is the front page, and `_Sidebar.md` is the navigation beside every page, so a
  new page gets a line there.
- **Links between pages** use the page name without the extension:
  `[Configuration](Configuration)`.
- **Images** go in `images/` and are referred to by a relative path, `images/postulo.png`;
  Forgejo serves them from the wiki repository.

Please keep it honest: the wiki says plainly what is not built yet, and a page that
describes a feature which does not exist is a bug.

Three pages are held to the code by `tests/test_wiki_surface.py` whenever the wiki is
checked out beside this repository: *The API* lists every call with its scope, *Health,
metrics and logs* every endpoint for machines and every metric, and *Writing a plugin*
every name `postulo.plugins.api` promises and every kind of plugin. A new call, metric or
name fails the suite until its line is written.

## Translations

Postulo is written in British English and translated from there. French and Portuguese
catalogues exist and are waiting for contributors — see
[docs/TRANSLATING.md](docs/TRANSLATING.md).

## Keeping dependencies current

Once a month, or when the `security` job says so: `uv lock --upgrade`, run the suite,
read the changelogs of anything that moved a major version, and commit the lock file on
its own. `docs/THREAT-MODEL.md` says who the attackers are and which rules follow; a pull
request that touches a boundary answers to it.

## Writing a changelog entry

An entry says **why**, not what. The diff already says what. Each goes under *Unreleased*,
in one of six sections, each marked so the file can be scanned rather than read:

| | For |
| --- | --- |
| `### ✨ Added` | a capability that was not there |
| `### 🔧 Changed` | behaviour that existed and is now different |
| `### 🐛 Fixed` | a defect |
| `### 🔒 Security` | anything with a security consequence |
| `### ⚠️ Deprecated` | on its way out |
| `### 🗑️ Removed` | gone |

The word stays beside the mark: the word is what reads in a terminal, to a screen reader,
and in a font that has no glyph for the mark. `tests/test_changelog.py` refuses a heading
that is not one of these, or one carrying the wrong mark.

End the entry with the issue it closes, in brackets: `(#42)`.

## Making a release

1. `uv run pytest -m release` — the promises that must hold in a published version rather
   than in every commit. Today that is the twenty-four European Union catalogues being
   complete. Ordinary work translates English, French and European Portuguese; **this is
   where the rest are swept up**, and it is the only check between an unfinished catalogue
   and a published version. Fill what it names (`scripts/messages.py extract` first if the
   strings are new) and run it again until it passes.
2. Move the *Unreleased* entries in `CHANGELOG.md` under a new `## [X.Y.Z] — YYYY-MM-DD`
   heading, and leave an empty *Unreleased* above it.
3. Set the same version in `pyproject.toml` and in `src/postulo/__init__.py`.
4. `python scripts/release_tools.py check vX.Y.Z` says whether the three agree.
5. Commit, then tag and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The `release` workflow does the rest, and it is one job: it refuses a tag that disagrees
with the code or the changelog, builds the sdist and the wheel, and creates the Forgejo
release with the changelog section as its notes.

### Versions of official plugins

A plugin the project publishes -- postulo-mcp, postulo-paperless, the rest under the
`postulo` organisation -- carries the version of the Postulo it was released beside:
postulo-mcp 0.3.0 ships with Postulo 0.3.0. Somebody who knows which Postulo they run
knows which plugin to install, without a compatibility table. The patch is the plugin's
own: 0.3.4 is the fifth fix to the 0.3 plugin and implies no core release.

What the installer enforces is the **major**, through `requires_postulo` in the catalogue:
an official plugin declares `>=0.3,<1.0` and keeps installing across core's minors until
the next major. During 0.x that is loose on purpose -- 0.x is where breaking changes land
in the minor -- so a plugin a core release actually breaks raises its own floor (`>=0.6`)
and says so in its changelog. That is the mechanism, not the installer. The two browser
extensions carry the same number in `package.json`; nothing installs them from a
catalogue, so nothing enforces it there. `postulo-templates` is not a package and carries
no version at all, deliberately: a template pack is copied, not installed.


Every push to `main` builds an image and publishes it as **`:dev`**, alongside a pinnable
`:<version>-dev.<short sha>` — `dev-image.yml`. It exists so a change can be run somewhere
real before it is in a release, and it is **not** a release: unsupported, never `:latest`,
and free to change a database in ways a release will not. Quote the pinned tag in a bug
report; `:dev` moves and says nothing about what somebody was running.

It is scanned by the same `scripts/scan-image.sh` a release is, because a dev image somebody
runs against their own applications is an image. It is built for `linux/amd64` and
`linux/arm64`, as the release is, because the instance these images exist to be run on is a
Raspberry Pi — an amd64-only dev image would be one nobody could deploy.

**Old ones are pruned.** Every push adds a pinned tag, so the job ends by running
`scripts/prune-dev-images.py`: the newest five pinned dev tags stay (`DEV_IMAGES_KEPT` in the
workflow) and the rest go, together with the per-architecture manifests only they referenced
— a manifest nothing names still holds its layers. Releases, `latest` and `dev` are never
candidates, and nothing untagged that the run did not itself orphan is touched. Run it by
hand with `--dry-run` to see what it would do; it needs `FORGEJO_USER` and `FORGEJO_TOKEN`
in the environment, never on the command line.

**Why this may run on a push when the release image may not.** The runner answering the
`docker` label is a *second runner instance registered to this repository alone*, so Forgejo
will not schedule another repository's jobs onto it. That is what makes an automatic trigger
acceptable — enforced by the instance, rather than by every workflow author remembering not
to. Re-read that before widening the trigger.

**The container image is a separate, deliberate act.** `image.yml` is started by hand, and
it needs a runner advertising the `docker` label plus `REGISTRY_USER` and `REGISTRY_TOKEN`
as secrets. It is not on the tag trigger, because Forgejo schedules a job before it evaluates the condition that would
skip it: a job asking for a label no runner advertises queues for ever and its run never
finishes, which is how v0.2.0's release came to look unfinished long after it was published
(#81). A workflow nobody starts cannot queue. Without a docker runner,
`scripts/check-image.sh` builds and checks the image wherever there is a Docker daemon.

**The image is scanned before it is pushed.** `scripts/scan-image.sh` runs Trivy and Grype
over it, and `image.yml` calls that same script rather than describing the intent a second
time. Two things about how it is set up are deliberate:

- **Both scanners.** They disagree usefully. On the image that produced #155 and #157, Grype
  found the only actionable Debian update, which Trivy did not mark fixable; Trivy found the
  Python packages and a leftover uv cache, which Grype's deb-and-binary scan did not see at
  all. Running one and believing it would have missed half of it.
- **The gate is fixable findings, not severe ones.** Six CRITICALs with no fix available is
  the normal state of a Debian base image, and a build that fails every day for reasons
  nobody can act on gets switched off within a fortnight. What stops a push is something
  somebody can do something about.

The unfixable half is reported rather than hidden, along with the secret and misconfiguration
scans — a scan that records only its failures throws away the half saying the image is in the
state you think it is. Each run keeps a CycloneDX bill of materials, which is more use to
somebody self-hosting Postulo than this run's verdict: it lets them scan the release later,
against a database that does not exist yet.

Both scanners download a vulnerability database, so the step needs the network.

**Three outcomes, not one red step.** The script exits 0 when nothing fixable was found, 1
on fixable findings — the gate — and 2 when the scan did not complete: a scanner that failed
to run, a bill of materials that came back empty. The last is not a finding and must not
read as one; it says nothing about the image, and says so. `.scan/verdict.txt` holds which
of the three it was (`clean`, `findings`, or `incomplete: <why>`), and both image workflows
put it at the top of their run summary. `tests/test_scan_image.py` drives all three through
a fake `docker`, so the reading of the scanners is tested without running them (#192).

### Giving a runner the `docker` label

**Not `docker:host`, if the runner is itself a container.** That was the advice here until
#190 and it does not work: `host` runs the job *inside the runner container*, and
`code.forgejo.org/forgejo/runner` is Alpine with no node and no docker CLI, so
`actions/checkout` — a JavaScript action — fails before anything reaches the daemon. The
recipe assumed a runner installed on the host.

What works, and what `ouranos` runs, is a **container label pointing at an image that
already has the tools**, with the socket mounted into job containers:

```yaml
runner:
  labels:
    - "docker:docker://catthehacker/ubuntu:act-latest"
container:
  docker_host: "automount"
```

`catthehacker/ubuntu:act-latest` ships node, git, the docker CLI and buildx, so there is no
custom image to build and keep current. `automount` puts `/var/run/docker.sock` into the job
container.

**Use a second runner instance, not a label on the one you have.** This is the part that
matters, and it is not obvious:

- **`container.docker_host` is per runner instance, not per label.** Setting `automount` on
  a runner that also serves the ordinary `ubuntu-*` labels hands the socket to *every* job
  it runs — and Docker is root on that machine. There is no way to scope it to one label.
- **Register the second runner to one repository** (`--scope owner/repo`). Forgejo then
  refuses to schedule anything else onto it. That is a stronger guarantee than "nothing
  schedules onto this label by itself", because it is enforced by the instance rather than
  by every workflow author remembering.

With that, an automatic trigger becomes reasonable: `dev-image.yml` builds on every push to
`main`. Without it — a shared runner with `automount` — it is not, and the trigger is the
first thing to reconsider if the runner arrangement ever changes.

QEMU binfmt is still needed on the **host** for the `linux/arm64` half of both images:
`docker run --privileged --rm tonistiigi/binfmt --install all`.

### Starting the image workflow

Two refs are involved and they are not the same one, which is worth saying because getting
it wrong produces an error that explains nothing:

```
GetWorkflowFromCommit, workflow not found
```

Forgejo reads a dispatched workflow **from the ref you dispatch it from**, and `image.yml`
has only existed since after v0.2.1. Choosing a tag in the branch selector therefore looks
for the file in that tag's tree, does not find it, and says the above. So:

- **dispatch from a branch that has the file** — the branch selector;
- **name the tag to build in the `tag` input** — the box on the form.

The workflow's own checkout uses `ref: ${{ inputs.tag }}`, so the workflow comes from the
branch while the code built comes from the tag.

Two secrets have to exist first, on the repository or its organisation: `REGISTRY_USER` and
`REGISTRY_TOKEN`, the latter a token with `write:package`. Without them the sign-in step
fails on an empty password, which the log reports as a login failure rather than as a
missing secret.

To check a runner works before a release depends on it, dispatch from a branch and give it
the current release's tag. That builds something real, publishes tags that are true, and
does not need a new tag cut for the purpose.

### Scanning the image

Nothing scans the image yet — that needs a runner that can build one, and is #156. Until
then it is a step somebody does, and this is the step:

```sh
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    aquasec/trivy image --ignore-unfixed postulo:latest
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    anchore/grype postulo:latest --only-fixed
```

**`--ignore-unfixed` / `--only-fixed` is the point, not a convenience.** A Debian stable
base image carries dozens of HIGH and several CRITICAL findings with no fix available —
that is the normal state of Debian stable, where the security team triages a great many as
no-DSA. A gate on severity is therefore red every day for reasons nobody can act on, and is
switched off within a fortnight. A gate on *fixable* findings is one somebody can clear, and
on the image that produced #155 it was a single line.

**Run both.** They disagreed usefully: Grype found the only actionable Debian update, which
Trivy did not mark fixable at all; Trivy found the Python packages and a leftover uv cache,
which Grype's scan did not see. They also disagree about severity, because Trivy prefers the
distribution's rating of how a CVE affects *its* build and Grype leans on the NVD's. Neither
number is the number, and anything written into a pipeline has to say which tool it means.

**Write down the clean half too.** The scan that produced #154 and #155 also reported zero
secrets and zero misconfigurations, and that is the result that gets forgotten when only the
bad news is recorded.

A fixable finding in a Debian package usually means the image needs rebuilding without a
cache rather than a code change: `apt-get upgrade` runs at build time (see `docker/Dockerfile`
and *Configuration → The image and Debian's updates*), so a fresh build takes whatever Debian
has published since.

## A plugin that needs consent

`FieldSpec` covers everything a person can type. For a provider that wants OAuth instead,
declare what is being asked for and Postulo conducts the round trip:

```python
from postulo.plugins.base import Consent, FieldSpec


class MyPlugin:
    def config_fields(self) -> list[FieldSpec]:
        # The client id and secret are still fields: they identify the *instance* to the
        # provider, and an operator registers them by hand.
        return [
            FieldSpec("client_id", "Client id"),
            FieldSpec("client_secret", "Client secret", type="password", secret=True),
        ]

    def needs_consent(self) -> Consent:
        return Consent(
            authorise_url="https://provider.example/authorise",
            token_url="https://provider.example/token",
            scopes=("https://provider.example/auth/send",),
            provider="Provider",
            extra={"access_type": "offline"},  # Google needs this to issue a refresh token
        )
```

Postulo takes it from there: the redirect, the signed state, the code exchange, the encrypted
tokens, and a refresh before every use. Ask for the token with
`postulo.plugins.consent.access_token(connection)` — never keep one yourself, and never read
the stored secrets directly, because refreshing is the part that has to happen at the moment
of use.

**Ask for the narrowest scope that does the job.** The scopes are shown to the person on the
page before they agree, which is the point: a list nobody can read is a list nobody consented
to.

**Do not reach for the sign-in tokens.** allauth holds one for anybody who signed in through a
provider, and it carries the scopes asked for at sign-in. Reusing it would mean asking for a
mail scope at sign-in on the chance it might be useful later, which is precisely the
over-broad consent this project should not teach — and allauth holds one token per account per
application, so a second grant has nowhere to sit beside the first.

## A plugin that owns a table

Most plugins own nothing. A source reads a page, an importer reads a file, a transport carries
a message; none of them keeps anything, and any of them can be moved out of core without a
further thought. **A plugin that owns a model is a different animal**, and there is one rule.

> A plugin that owns a table may not be uninstalled while that table holds anything.

Postulo refuses, says how many records are in the way, and leaves both the package and the
data alone. Two other answers were open and neither is safe. *Uninstall and keep the table*
leaves data nothing can read, export or restore — the failure the export exists to prevent.
*Uninstall and delete, behind a confirmation* makes removing a package a data-destroying act:
somebody swapping a plugin for a newer build of the same plugin loses everything it held, and
Postulo promises in thirty-nine languages that switching a plugin **off** deletes nothing.
Putting *uninstall* on the other side of that promise is a distinction nobody holds in their
head at the moment it matters.

**Off and uninstalled are now different acts.** Off keeps everything and offers nothing.
Uninstalled takes the code away, and is refused while there is anything to take away with it.

What a plugin that owns a model has to do:

```python
class MyPlugin:
    name = "my-plugin"
    #: Django labels. Postulo counts these before letting the package go.
    owns_models = ("my_plugin.Thing",)

    def export_for(self, person) -> list[dict]:
        """This person's rows, for their archive. See below."""
        return [{"what": row.what} for row in Thing.objects.filter(owner=person)]
```

**`export_for` is not optional in spirit.** A plugin that owns a person's data and cannot put
it in their archive gets its models *named* in that archive under `not_carried`, because an
archive that is quietly incomplete is discovered when somebody restores it, and one that says
which part is missing is discovered while they still have the original. Write the method.

**Ship your own migrations, and be in `INSTALLED_APPS`.** Django needs the app loaded to see
the model at all. Because the package cannot be uninstalled while its table holds anything, the
table is always empty when the app goes — so its migrations reverse cleanly and there is never
a migration referring to a module that no longer imports.

**Emptying the table is your job to offer.** The refusal tells somebody to empty it from the
plugin's own pages; those pages are yours, and while the plugin is installed is exactly when
they can be asked.

## Licence

Contributions are accepted under the [AGPL-3.0-or-later](LICENSE) licence that covers
the project.
