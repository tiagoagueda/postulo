# The capture API

Postulo has one machine-readable surface, and it does one thing: accept a job posting for
review.

It exists so that something outside Postulo — a script, a keyboard shortcut, the browser
extension planned for later — can hand over a posting without you copying anything by
hand.

## What it cannot do

Worth stating plainly, because it is the point. A capture token **cannot**:

- read your applications, CVs, cover letters, files or contacts;
- change or delete anything;
- sign in to the web interface.

It can create captures, and list the ones it created. A token that leaks costs its holder
the ability to put entries in a review queue that you will then decline.

## Getting a token

**Settings → Capture tokens.**

Give it a name — the device or tool it is for — and Postulo shows you the token **once**.
Only a hash is stored, so it genuinely cannot be shown again. If you lose it, revoke it
and make another.

Tokens can be revoked at any time, and the list shows when each was last used, which is
how you notice one you forgot about.

## Using it

Send the token as a bearer token.

**Check that a token works.** Creates nothing, so it is safe to call while you are
setting something up:

```sh
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://postulo.example.org/api/v1/me
```

```json
{ "name": "Firefox on the laptop", "owner": "you@example.org", "last_used_at": null }
```

**Hand over a posting by address.** Postulo fetches the page itself, under the same rules
as the web interface:

```sh
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.org/jobs/42"}' \
  https://postulo.example.org/api/v1/captures
```

**Hand over a posting with its page.** Supply `html` and Postulo parses what you send
instead of fetching anything. This is how a browser extension captures a posting that is
only visible to a signed-in reader — the browser has already loaded it, so there is
nothing for the server to fetch and no login for it to lack:

```sh
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.org/jobs/42", "html": "<html>…</html>"}' \
  https://postulo.example.org/api/v1/captures
```

Either way the response is the capture, including where to review it:

```json
{
  "id": 12,
  "url": "https://example.org/jobs/42",
  "title": "Senior Backend Engineer",
  "company_name": "Aperture Science",
  "location": "Paris, FR",
  "source": "schema.org",
  "status": "pending",
  "created_at": "2026-09-04T18:26:40Z",
  "review_url": "https://postulo.example.org/jobs/captures/12/review/"
}
```

**List what is waiting.**

```sh
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://postulo.example.org/api/v1/captures
```

## Responses

| Status | Meaning |
| --- | --- |
| `201` | The capture was created and is waiting for review |
| `401` | The token is missing, wrong, or revoked |
| `422` | The page could not be fetched or held nothing resembling a posting. The body says which |

A `422` is an explanation, not a failure to retry blindly: it will say that the address is
private, that `robots.txt` declined, that the page was too large, or that nothing job-like
was found.

## Nothing is created but a capture

The API never creates an application. Everything it accepts waits for you on the review
screen, for the same reason the web interface works that way: a parser reading somebody
else's markup is not a good enough reason to write to your records.
