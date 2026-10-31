# European Digital Credentials used as test fixtures (#670)

© European Union, from github.com/european-commission-empl/European-Learning-Model, commit
9d7c5d22002237c3afeb1750b7038e6fe2cdd371, path `Credentials/JSON-LD Examples (ELM v3)/` and
`rdf/samples/`, licensed EUPL-1.2 (compatible with AGPL-3.0 per the EUPL appendix).

| File | What it is |
| --- | --- |
| `github-ELM-Sample-CertOfPart-signed.jsonld` | A certificate of participation, sealed (JAdES, JWS JSON general serialisation). No EQF level. |
| `github-ELM-Sample-MC-Annex1-signed.jsonld` | A micro-credential, sealed. EQF level 6. |
| `github-ELM-Sample-MC-Annex1-unsigned.jsonld` | The same micro-credential, unsealed: the bare credential. |
| `github-ELM-rdf-credential-sample.jsonld` | The data model's own example, in English and French, unsealed. |

The files are kept byte for byte as they were published: a seal covers exact bytes, so they
are marked `-text` in `.gitattributes` and left out of the pre-commit hooks.
