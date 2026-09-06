# Backups and your data

## Taking everything with you

**Dashboard → Export everything**, on the *Shortcuts* widget.

One zip, holding a readable JSON document of every record in your account and every file
in it: your profile, career record, companies, postings, applications with their whole
timeline, reminders, interviews, tags, CVs, cover letters, uploads and every document you sent.

From the command line:

```sh
uv run manage.py export_data you@example.org --output postulo-backup.zip
```

The JSON is nested the way the records actually relate — companies contain postings,
postings contain applications, applications contain their timeline — rather than being a
dump of database tables. The point is that somebody can still do something useful with it
in ten years, when Postulo is a memory.

## Bringing a career record in

A Europass CV — either the JSON europass.europa.eu exports today or the XML the old CV
editor produced — can be read straight into your career record, in two steps, with nothing
written until you have seen what was found.
[Your career record](Your-career-record#importing-a-europass-cv) has the detail.

## Bringing a spreadsheet in

Most people track a job search in a spreadsheet until it hurts. **Settings → Your data →
Import from a spreadsheet** takes that spreadsheet as a CSV (from Excel, Numbers or Google
Sheets: *Save as* or *Download* → CSV; commas, semicolons or tabs; any encoding Excel
produces) and brings it in without retyping:

1. **Upload** the file, or download the template with Postulo's own columns first.
2. **Map the columns.** Postulo guesses which column is which from the header names — in
   English, French or Portuguese: *Entreprise* and *Empresa* are a company — and every
   guess can be corrected. Say once whether dates are written day-first or month-first.
3. **Check the preview** of the first rows as they will be read: dates parsed, statuses
   mapped from whatever the spreadsheet said (*entretien* is *interviewing*; a word Postulo
   does not know becomes *applied*, with the original kept in a note), and what each row
   becomes.
4. **Import**, in one transaction. Rows with a date applied become applications, dated as
   the spreadsheet says; rows without one become listings; companies are matched by name
   or created; tags are created as needed. Every imported application carries an *Imported
   from file.csv* entry on its timeline, so provenance is never in doubt.

A row already recorded — the same posting address, or the same company, role and date
applied — is reported and left alone, so importing twice does not double anything. The
report says how many rows became what and why any were skipped.

From the command line, for a long history:

```sh
uv run manage.py import_csv alex.morgan history.csv --show          # print the guessed mapping
uv run manage.py import_csv alex.morgan history.csv --mapping map.json --dry-run
uv run manage.py import_csv alex.morgan history.csv --mapping map.json
```

Excel's own format is not read; *Save as CSV* is one click, and it keeps a dependency out
of a one-time operation.

## Putting an archive back

```sh
uv run manage.py import_data you@example.org postulo-backup.zip
```

Import **creates records; it never merges**. Deciding whether the "Acme" in a file is the
same Acme already in the database is a judgement Postulo is not in a position to make,
and getting it wrong quietly would be worse than not trying. So an import into an account
that already holds a job search is refused unless you pass `--force`.

Two exceptions to "never merges", both deliberate:

- **Companies are matched by identifier first, then by name.** A company in the file that
  carries a Wikidata item (or any other id) your account already has attaches to that
  company whatever it is called; otherwise the name decides, the same rule the application
  form uses, so a forced
  import attaches to employers you already have rather than creating "Acme" twice.
- **A CV whose name is taken gets a number appended.** A CV is content rather than an
  identity, so a clash gets a new name instead of being merged into whatever happened to
  share its title.

It runs in one transaction: an archive that turns out to be broken half way through
leaves the account exactly as it was.

There is no import button in the web interface. Importing is a migration, not an everyday
action, and it is worth doing deliberately on a command line rather than by clicking.

## Backing up the whole instance

An export is one person's portable copy. A backup is the operator's copy of everything on
the instance — every account, the database and the media directory — taken consistently
while Postulo runs, in one archive:

```sh
uv run manage.py backup                      # into POSTULO_BACKUP_DIR, timestamped
uv run manage.py backup /backups/postulo.tar.gz
```

In the container, the same command through Compose, writing onto the data volume:

```sh
docker compose -f docker/compose.yml exec postulo python manage.py backup
```

The archive holds a manifest (Postulo version, engine, counts), the database — copied
through SQLite's own backup mechanism, or `pg_dump` for PostgreSQL, never by copying a
file that is being written to — and the media directory, file by file. Every archive is
verified after it is written: the manifest is read back and the database is checked
against its checksum. A backup that was never opened is a hope.

Put one on a schedule. A line of cron on the host, daily, with a retention your disk can
afford:

```cron
15 3 * * * docker compose -f /data/stacks/postulo/docker/compose.yml exec -T postulo python manage.py backup
```

The archive holds everyone's data on the instance, unencrypted. Keep it where the data
itself would be safe, and encrypt it in transit with whatever tool carries it off the
machine — restic, borg or age do this well; Postulo does not try to.

## Restoring

Onto an **empty** instance — install Postulo, run nothing else — then:

```sh
uv run manage.py restore /backups/postulo-backup-20260905-031500.tar.gz
```

It reads the manifest, refuses an archive from the other database engine, puts the
database back, writes the media files, and runs migrations so that a backup from an older
Postulo lands correctly on a newer one. An instance that already has accounts is refused
unless you pass `--force`, which replaces everything on it; media files that already
exist are kept unless `--force` is given too.

`POSTULO_BACKUP_DIR` is where `backup` writes with no target — `data/backups` by default,
`/app/data/backups` in the container, beside the data it copies. Move it if that volume
is the thing you are backing up.

## Copying the files by hand

If you would rather not use the command, the pieces are plain files:

Two things, and they must be copied together:

1. **The database.**
   - SQLite (the default): `data/postulo.sqlite3`
   - PostgreSQL: a `pg_dump` of your database
2. **The media directory**, `data/media` by default. This holds every uploaded file and
   every PDF snapshot. A database without it will show you a list of documents that no
   longer exist.

`.env` is worth keeping too. Losing `POSTULO_SECRET_KEY` will not lose your data, but it
will log everyone out.

## Backing up SQLite properly

Do not copy the file while the application is running. SQLite has a command that takes a
consistent copy safely:

```sh
sqlite3 data/postulo.sqlite3 ".backup '/backups/postulo-$(date +%F).sqlite3'"
tar czf /backups/postulo-media-$(date +%F).tar.gz -C data media
```

Or stop Postulo, copy both, and start it again.

## PostgreSQL

```sh
pg_dump --format=custom postulo > /backups/postulo-$(date +%F).dump
tar czf /backups/postulo-media-$(date +%F).tar.gz -C data media
```

## Restoring by hand

1. Install Postulo at the same version the backup came from.
2. Put the database file back, or `pg_restore` the dump.
3. Unpack the media directory into place.
4. Run `uv run manage.py migrate` — it will do nothing if the versions match, which is
   what you want to see.

## Getting your data out in a readable form

Everything is in a plain SQLite file, which is about as portable as data gets: `sqlite3`,
any database browser, or a few lines of Python will read it. Django can also dump the lot
as JSON:

```sh
uv run manage.py dumpdata --natural-foreign --indent 2 > postulo.json
```

That covers the records but not the files; take the media directory as well.

## What is deliberately not backed up

`staticfiles/` is generated by `collectstatic` and can be rebuilt. `data/.dev-secret-key`
is a development convenience. `.venv/` and `node_modules/` are dependencies.
