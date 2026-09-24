# Where `esco-<revision>.json` came from, and on what terms

Postulo keeps a copy of the ESCO classification in `src/postulo/jobs/data/` as
`esco-<revision>.json`: the **ISCO-08** unit groups that structure the **ESCO**
classification — the European Commission's classification of skills, competences,
qualifications and occupations — and the ESCO occupations mapped to them, with the name
of each in every language the classification is published in. (The counts and the
language list are pinned by `tests/test_esco.py`, against the published shape of v1.2.1:
3,039 occupations, 436 unit groups, 28 languages.) It is not committed: a few megabytes
of reference data is not a repository's job to carry, and `manage.py fetch_esco` is how
it gets into `data/`.

**Who publishes it.** The Directorate-General for Employment, Social Affairs and
Inclusion of the European Commission maintains ESCO and publishes it, free of charge, in
SKOS-RDF and CSV, on <https://esco.ec.europa.eu>. The classification is built on ISCO-08,
the International Standard Classification of Occupations, whose hierarchy — major,
sub-major, minor and unit group — it reuses for its first four levels.

**Under what licence.** The ESCO services publish their classification and software
under the **European Union Public Licence (EUPL) 1.2**, a free licence whose conditions
are met by the attribution below.

**Attribution, as required.**

> Source: European Commission, Directorate-General for Employment, Social Affairs and
> Inclusion, *ESCO — European Skills, Competences, Qualifications and Occupations*,
> v1.2.1, published at https://esco.ec.europa.eu. © European Union, 2025. Licensed
> under the EUPL 1.2.

**Changes made, as required.** Only shape, never wording. The file holds the unit groups
and the occupations, not the skills and competences and qualifications that make up the
rest of the classification — see `postulo/jobs/esco.py` for why the unit group is the
level this uses. Nothing was translated, renamed, merged or added.

**How the file gets there, and how it is replaced.** The command asks the ESCO
web-service API — the machine-facing access the ESCO services document, on the portal
under *Use ESCO → Use ESCO Services (API)* — for every unit group and occupation in
every language, checks the answer against the shape the tests pin, and writes
`esco-<revision>.json` beside the code that reads it. The revision is recorded inside
the file rather than in a variable name, so whatever `esco-*.json` is in `data/` is what
runs. There is still no step in a request that reaches for the internet for reference
data, and without the file Postulo runs on with no codes. To replace it: run the command
with the next version, bump the tests to the revision they now hold, and delete the file
being replaced — a code a person's record holds and the new revision has gone is a
record that keeps its name and loses its code, which is the correct outcome and is what
`esco.name_for` already does.
