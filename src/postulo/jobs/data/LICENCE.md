# Where `nace-2.1.json` came from, and on what terms

**What it is.** The sections and divisions of **NACE Rev. 2.1** — the European Union's
statistical classification of economic activities — with the name of each in every official
EU language. 22 sections (A–V), 87 divisions (two digits), 24 languages.

**Who publishes it.** Eurostat maintains NACE; the Publications Office of the European Union
publishes it as Linked Open Data on EU Vocabularies. This file was taken from that copy,
through the SPARQL endpoint at <https://showvoc.op.europa.eu>, with the concept scheme
`http://data.europa.eu/ux2/nace2.1/`.

**Under what licence.** Reuse of European Commission documents is authorised under
**Commission Decision 2011/833/EU of 12 December 2011**, and the Commission's default
licence for content it owns is **Creative Commons Attribution 4.0 International (CC BY
4.0)**: reuse is permitted for any purpose, commercial included, provided appropriate credit
is given and changes are indicated. That is compatible with redistributing it here.

The Decision excludes software, trademarks, logos and names from its scope, and third-party
material may carry its own terms. Neither applies to this file: it is Commission-owned
reference data, no mark or logo travels with it, and nothing in it is third-party work.

**Attribution, as required.**

> Source: Eurostat, *NACE Rev. 2.1 — Statistical classification of economic activities in
> the European Community*, published by the Publications Office of the European Union.
> © European Union, 2024. Licensed under CC BY 4.0.

**Changes made, as required.** Only shape, never wording. Eurostat prefixes each label with
its own code (`62 Computer programming, consultancy and related activities`); the code is
stored separately here and stripped from the front of the name. Groups (three digits) and
classes (four) were not taken — see `postulo/jobs/industries.py` for why the division is the
level this uses. Nothing was translated, renamed, merged or added.

**Keeping it current.** NACE Rev. 2.1 replaced Rev. 2 after sixteen years, and there will be
a Rev. 3. The revision is recorded inside the file rather than in a variable name, and
`scripts/` has no step that regenerates it: the harvest is a deliberate act, and the script
that did it is in the issue (#140) rather than in the repository, because a build step that
fetches from the internet is a build step that fails when somebody else's server does.

To replace it: harvest into a file of the same shape, bump `revision`, and check that every
`Industry.code` in an existing database still names a division — a code that has gone is a
person's industry that keeps its name and loses its code, which is the correct outcome and
is what `industries.name_for` already does.
