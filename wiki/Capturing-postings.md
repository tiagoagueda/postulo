# Capturing postings

Rather than retyping an advert, paste its address and let Postulo read the page.

**Capture a posting**, on the dashboard.

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

## When the site refuses

Large employers frequently sit behind bot protection that turns away anything which is
not a browser. You will see something like:

> The site refused the request (403). Large sites often sit behind bot protection that
> turns away anything that is not a browser, even when the page is perfectly visible to
> you. Paste the page source in below instead.

The advert is on your screen and unreachable from your server at the same time, and that
is not a contradiction: the block is aimed at automated clients in general, not at you.

Postulo does not pretend to be a browser to get around it. Disguising the request would
be dishonest, and it would also break `robots.txt` handling, which matches on the name a
client gives — so the polite thing and the correct thing happen to agree.

**Paste the page source instead.** On the capture page, open *The site refuses Postulo,
or the posting needs a login*:

1. Open the posting in your browser.
2. View the page source (Ctrl+U in most browsers) and select all of it.
3. Paste it into the box, along with the address.

Postulo reads what you paste and fetches nothing. Your browser was already allowed to see
the page, so there is nothing to refuse.

The same route works for postings behind a login, and for pages built entirely by
JavaScript — in the second case, use your browser's developer tools to copy the rendered
HTML rather than the original source, since the source will not contain the advert.

This is exactly what the planned browser extension will automate.

## When nothing can be read at all

If a page carries no structured data and no usable title, use **Record an application**
and type it in. Same form, without the pre-filling.

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
