# Where the ESCO files came from, and on what terms

Postulo keeps a copy of the ESCO classification in `src/postulo/jobs/data/`, in two files:

- `esco-<revision>.json`: the **ISCO-08** unit groups that structure the **ESCO**
  classification — the European Commission's classification of skills, competences,
  qualifications and occupations — and the ESCO occupations mapped to them, with the name
  of each in every language the classification is published in. (The counts and the
  language list are pinned by `tests/test_esco.py`, against the published shape of v1.2.1:
  3,039 occupations, 436 unit groups, 28 languages.)
- `esco-skills-<revision>.zip`: the ESCO **skills**, the class the classification's API
  names `skill`, which holds the knowledge concepts beside the skills and competences —
  13,939 of them in v1.2.1 — each as its identifier and its preferred name in each of the
  same 28 languages. About 4.7 MB as it is kept, compressed; one language's names are read
  at a time (#266).

Neither is committed: megabytes of reference data are not a repository's job to carry, and
`manage.py fetch_esco` is how they get into `data/`.

**Who publishes it.** The Directorate-General for Employment, Social Affairs and
Inclusion of the European Commission maintains ESCO and publishes it, free of charge, in
SKOS-RDF and CSV, on <https://esco.ec.europa.eu>. The classification is built on ISCO-08,
the International Standard Classification of Occupations, whose hierarchy — major,
sub-major, minor and unit group — it reuses for its first four levels.

**Under what licence.** The ESCO services publish their classification and software
under the **European Union Public Licence (EUPL) 1.2**, a free licence whose conditions
are met by the attribution below. The skills add no obligation the occupations did not
have: the same licence, the same publisher, and still a copy each instance downloads for
itself rather than one this project hands on.

**Attribution, as required.**

> Source: European Commission, Directorate-General for Employment, Social Affairs and
> Inclusion, *ESCO — European Skills, Competences, Qualifications and Occupations*,
> v1.2.1, published at https://esco.ec.europa.eu. © European Union, 2025. Licensed
> under the EUPL 1.2.

**Changes made, as required.** Only shape, never wording. The first file holds the unit
groups and the occupations — see `postulo/jobs/esco.py` for why the unit group is the
level a title keeps. The second holds each skill's identifier and its preferred names and
nothing else: no alternative names, no descriptions, no relations between skills, and none
of the qualifications. Space at either end of a skill's name, and a line break inside one,
are taken out, because a line of that file is one name; no letter is changed, and the
no-break spaces inside a name stay where the classification put them. Nothing was
translated, renamed, merged or added.

**How the files get there, and how they are replaced.** The command asks the ESCO
web-service API — the machine-facing access the ESCO services document, on the portal
under *Use ESCO → Use ESCO Services (API)* — for every unit group, occupation and skill in
every language, checks the answer against the shape the loader reads, and writes both files
beside the code that reads them, or neither. The revision is recorded inside each file
rather than in a variable name, so whatever `esco-*.json` and `esco-skills-*.zip` are in
`data/` are what runs. There is still no step in a request that reaches for the internet
for reference data, and without the files Postulo runs on with no codes and no recognised
skills. To replace them: run the command with the next version, bump the tests to the
revision they now hold, and delete the files being replaced — a code or a skill a person's
record holds and the new revision has gone is a record that keeps its name and loses what
the classification said about it, which is the correct outcome and is what `esco.name_for`
and `esco.skill_name` already do.
