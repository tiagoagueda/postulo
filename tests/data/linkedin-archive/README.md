# A LinkedIn data archive, made up (#1260)

The CSV files of a LinkedIn data archive for one invented person, *Alex Morgan*, at invented
companies under `example.org`. They are **not** from anybody's real export.
`tests/test_linkedin_archive.py` builds the ZIP from them with `zipfile`, in a top folder as
LinkedIn sends one, so no binary is kept here.

Their header rows are not taken from a real archive either: LinkedIn publishes no column
specification, so they were put together from LinkedIn's help page *Download your account
data* and from the header rows in the fixtures and parsers of open-source readers of the
archive (Reactive Resume, applyspace, linkedin-csv.rs), read for the names only.
`Positions.csv` carries a column the importer does not know (`Employment Type`), on purpose.
`Connections.csv` and `messages.csv` are there to prove they are never opened.
