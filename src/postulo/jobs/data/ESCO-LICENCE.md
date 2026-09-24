# Where `esco-1.2.1.json` came from, and on what terms

**What it is.** The **ISCO-08** unit groups that structure the **ESCO** classification —
the European Commission's classification of skills, competences, qualifications and
occupations — and the ESCO occupations mapped to them, with the name of each in every
language the classification is published in. (The counts and the language list are pinned
by `tests/test_esco.py`, against the published shape of v1.2.1: 3,039 occupations, 436
unit groups, 28 languages.)

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

**Keeping it current.** ESCO is versioned, and the revision is recorded inside the file
rather than in a variable name. The harvest is a deliberate act, done once, from the
official download page, and there is no step in this project that reaches for the
internet for reference data — a build step that fetches from the internet is a build step
that fails when somebody else's server does. To replace it: download the next version
from the same page, reshape it the same way, bump `revision`, and check that every code
a person's record holds still names a unit group — a code that has gone is a person's
record that keeps its name and loses its code, which is the correct outcome and is what
`esco.name_for` already does.
