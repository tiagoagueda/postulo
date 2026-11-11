# The JSON Resume schema, used to check what Postulo writes (#1100)

`schema.json` is the JSON Resume schema, version 1.2.1, from
github.com/jsonresume/resume-schema at the tag `v1.2.1` (commit
50798e359292ad4448d95b3bb0de5f694d6bcc4b), file `schema.json`. Its licence is MIT, in
`LICENSE.md` beside it, as the repository publishes it (compatible with AGPL-3.0).

It is kept as it was published (SHA-256
`8911e912ee487954b10cb59da39265c7e62ef7cba5973706d125448adc853969`), and left out of the
pre-commit hooks so that nothing reformats it. `tests/test_cv_structured_formats.py` reads
it from here: no test fetches anything.

The Europass JSON Schema (3.3.0, Cedefop) is **not** kept here: no licence for it has been
found, so the test holds the Europass file to the constraints of that schema it relies on,
written out in the test and named there.
