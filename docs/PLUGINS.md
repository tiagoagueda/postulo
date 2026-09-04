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

## A worked example

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
