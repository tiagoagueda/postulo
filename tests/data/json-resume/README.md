# The JSON Resume schema, used to check what Postulo writes (#1100) and reads (#1260)

`schema.json` is the JSON Resume schema, version 1.2.1, from
github.com/jsonresume/resume-schema at the tag `v1.2.1` (commit
50798e359292ad4448d95b3bb0de5f694d6bcc4b), file `schema.json`. Its licence is MIT, in
`LICENSE.md` beside it, as the repository publishes it (compatible with AGPL-3.0).

It is kept as it was published (SHA-256
`8911e912ee487954b10cb59da39265c7e62ef7cba5973706d125448adc853969`), and left out of the
pre-commit hooks so that nothing reformats it. `tests/test_cv_structured_formats.py` reads
it from here: no test fetches anything.

`schema-1.7.1.json` is the current schema, version 1.7.1, from the npm package
`@jsonresume/schema` 1.7.1 (https://registry.npmjs.org/@jsonresume/schema/-/schema-1.7.1.tgz,
SHA-1 of the package `9e197c768e7b3d1edb5a9f87232ee144798e283f` as the registry states it),
file `package/schema.json`. The repository the schema lived in was archived on 2026-06-12;
the package is published from `packages/schema` of github.com/jsonresume/jsonresume.org. Its
licence is MIT, in `LICENSE-1.7.1.md` beside it, as the package ships it. Kept as published
(SHA-256 `549b0880ebc1516963c6cc88dd4aa99d259ed867211586421425950dea94ddbd`).

The three `resume-*.json` files are the importer's fixtures (`tests/test_json_resume_import.py`),
written by hand for a made-up person, *Alex Morgan*, at made-up companies under `example.org`:
`resume-1.0.0.json` holds the classic fields and is valid against `schema.json`;
`resume-1.7.1.json` holds what 1.4.0 to 1.7.1 added and is valid against `schema-1.7.1.json`;
`resume-odd.json` holds values of the wrong kind, on purpose, and is valid against nothing.

The Europass JSON Schema (3.3.0, Cedefop) is **not** kept here: no licence for it has been
found, so the test holds the Europass file to the constraints of that schema it relies on,
written out in the test and named there.
