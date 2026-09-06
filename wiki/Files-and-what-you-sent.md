# Files and what you sent

Two different things live here, and the difference matters.

## Files you already had

**Documents → Files**

Upload PDFs, Word documents, certificates, portfolio pieces — anything written outside
Postulo. A CV a designer made for you belongs here, not in the CV builder.

**Versions.** When you upload a replacement, name the file it *supersedes*. Postulo
numbers the new one automatically and keeps the old file: superseded versions are shown
greyed out rather than deleted. You applied with that old version, and the record of what
you sent has to stay true.

The upload limit is 20 MB per file.

## What you sent

**Documents → Sent documents**

Whenever you export a CV, or record what you sent with an application, Postulo stores:

- the **PDF exactly as it was rendered** at that moment,
- the **text it was built from**, and
- a **checksum** of the file.

These are never regenerated. Edit the CV afterwards as much as you like; the snapshot
does not move.

This is the feature you will not appreciate until an interviewer asks about something on
your CV three months and eleven revisions later. The stored text matters as much as the
PDF, because a PDF is awkward to search and impossible to diff.

To record a set: open an application and choose **Record what you sent**. Pick a CV, a
cover letter and any files you already had. Postulo renders the first two as they stand,
attaches everything to the application, and notes it on the timeline.

## Links you pointed them at

Not everything you send is a file. A portfolio, a profile or a video CV is an address, and
those live on [your career record](Your-career-record) as **links**. *Record what you
sent* offers them beside your CV, your letter and your files, and the ones you tick are
attached to the application and named on the timeline. A link that did not answer when it
was last checked says so wherever it appears.

## Keeping copies elsewhere

Everything above lives in Postulo's own private media, and always will: that is where
rendering, downloads, the export and the review of what you sent read from, and none of
it needs a network. A **document store** is somewhere that receives *copies* — a
Paperless-ngx archive, say, through the
[postulo-paperless](https://source.tiagoagueda.com/postulo/postulo-paperless) plugin.

Once the operator has installed a store plugin, add it under **Settings → Connections**.
The form ends with a switch per kind of document — CV, cover letter, certificate,
portfolio, reference, other — so you can send the paperwork and keep the rest at home.
From then on every new document is queued for the store and the **scheduler** sends it on
its next pass, a few minutes later. Each document shows how that went, beside its name:

- **archived** — with a link to it in the store, when the store has one;
- **waiting to be sent** — the scheduler has not been round yet;
- **failed: …** — with the reason; Postulo tries again with a growing wait, six times,
  then leaves it to you;
- **not accepted** — the store declined that kind of document.

**Send to stores now** under a document tries at once, and gives a copy that gave up its
attempts back. **Send everything** on the connection queues every document you already
had before the store existed. Nothing is ever sent twice to the same store, and nothing
is ever deleted from a store: an archive is for keeping.

The references — where each copy went — travel in your export, so a restored instance
still knows where its copies are even before you recreate the connection.

## Privacy

Uploaded documents contain your address, your phone number and your full employment
history. Postulo treats them accordingly:

- **Media is never served by the web server.** Files are delivered only through a view
  that has already established who is asking. A file belonging to someone else is not
  merely refused — it is not found.
- Downloads are sent with `Cache-Control: private, no-store`, so they do not linger in a
  shared cache.
- Stored paths are checked on every request to confirm they resolve inside the media
  directory.

If you put a reverse proxy in front of Postulo, **do not** add a location block serving
`MEDIA_ROOT`. It would bypass every one of those checks. If you want the proxy to do the
work of sending bytes, use `POSTULO_MEDIA_ACCEL_PREFIX` instead, which hands over only
after Postulo has authorised the download. See [Configuration](Configuration#storage).
