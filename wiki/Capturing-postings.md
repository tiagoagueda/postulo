# Capturing postings

Rather than retyping an advert, paste its address and let Postulo read the page.

**Capture**, in the top right of every page.

## What happens

1. Postulo fetches the one page you pasted.
2. It reads what it can — usually from the structured data the site publishes about
   itself for search engines.
3. It shows you the result in the ordinary application form, for you to correct.
4. Nothing is recorded until you accept it.

That fourth step is not a formality. A parser reading markup it has never seen gets
things wrong, and a job title invented by a computer is exactly the sort of error you
would not spot six weeks later. Everything captured waits under **Captures** until you
have looked at it.

## What it can read

**Structured data.** Most large job boards embed a machine-readable description of the
posting for search engines. Postulo reads that. When a site provides it you get the
title, company, location, working arrangement, employment type, salary range, posting
date and closing date, all filled in.

**Whatever else the page says about itself.** With no structured data, Postulo takes the
page title and its readable text and leaves the rest to you. Less impressive, still less
typing than starting from nothing.

Where a field cannot be determined it is left **empty rather than guessed**. A blank box
invites you to type; a confidently wrong value has to be noticed before it can be
corrected.

## What it will not do

- **It does not crawl.** One page is fetched: the one you pasted. Postulo never follows
  links looking for more.
- **It honours `robots.txt`.** If a site asks automated clients not to fetch a page,
  Postulo does not. Copy the text in by hand instead.
- **It refuses private addresses.** Anything resolving to a loopback, private or
  link-local address is declined, and every redirect is rechecked. Postulo usually runs
  on a network with a router and a NAS on it, and a box that fetches any address you type
  is a way to go looking at them.
- **It gives up quickly.** Ten seconds, two megabytes, three redirects.

## When a page will not capture

Some pages cannot be read from the outside at all: postings behind a login, or built
entirely by JavaScript after the page loads. Postulo fetches HTML; it does not run a
browser.

When that happens, use **Record an application** and paste the text in. It is the same
form, without the pre-filling.

## Captures waiting for you

**Dashboard → Captures awaiting review**, or the Captures page.

Each capture can be reviewed — which opens the pre-filled form — or discarded. Accepting
one creates the application and links the two, so the record shows where it came from.

## Capturing from elsewhere

An instance can accept captures from outside: a script, a keyboard shortcut, or the
browser extension planned for later. See [The capture API](The-capture-API).

## Teaching Postulo about a particular site

If a board you use often is read badly, a plugin can be written for it and installed
without changing Postulo or waiting for anyone. See
[docs/PLUGINS.md](https://source.tiagoagueda.com/tiagoagueda/postulo/src/branch/main/docs/PLUGINS.md).

The capture page lists which sources are installed.
