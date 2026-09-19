# Third-party works in this repository

Postulo is Copyright (C) 2026 Tiago Agueda and is licensed under
[AGPL-3.0-or-later](LICENSE). Some of what the repository ships was written or drawn by
somebody else, under a licence of their own. This is the register of those works: where
each sits, whose it is, under what terms, and where its notice is. A copyright licence is
satisfied by carrying the notice, which is why the notice sits beside the files rather
than only being named here; a *trademark* is a different thing and has its own file,
[TRADEMARKS.md](TRADEMARKS.md).

`tests/test_trademarks.py` holds this table to the tree: a directory of somebody else's
work with no notice beside it, or a work committed here and not listed, fails the suite.

| Work | Where | Whose | Licence | Notice |
| --- | --- | --- | --- | --- |
| Lucide icons | `src/postulo/static/icons/` | [Lucide](https://lucide.dev) | ISC | Each file keeps its own `@license` comment on its first line. |
| flag-icons | `src/postulo/static/flags/` | [lipis/flag-icons](https://github.com/lipis/flag-icons) | MIT | `LICENSE.txt` beside the files. |
| Tailwind CSS | compiled into `src/postulo/static/css/app.css` | [Tailwind Labs](https://tailwindcss.com) | MIT | The `/*! tailwindcss … | MIT License */` banner on the stylesheet's first line, which the build writes. |
| basecoat-css | compiled into `src/postulo/static/css/app.css` (the components named in `assets/css/app.css`) | [Ronan Berder](https://basecoatui.com) | MIT | The `/*! basecoat-css … */` banner in the stylesheet, and `basecoat.LICENSE.txt` beside it. |
| htmx | `src/postulo/static/js/vendor/htmx.min.js` | [Big Sky Software](https://htmx.org) | 0BSD | `htmx.LICENSE.txt` beside it. 0BSD asks for no notice; it is carried all the same. |
| @zxcvbn-ts | `src/postulo/static/js/vendor/zxcvbn/` (core and two dictionaries) | Dan Wheeler and Dropbox, Inc.; [@zxcvbn-ts](https://github.com/zxcvbn-ts/zxcvbn) | MIT | `LICENSE.txt` beside the three files; the three packages carry one identical text. |
| Buy Me a Coffee banner and QR code | `assets/support/` | Buy Me a Coffee | A mark, not a licensed work | `NOTICE.txt` beside them; see [TRADEMARKS.md](TRADEMARKS.md). |

## How they get here, and how they stay right

Nothing above is fetched at run time: the content security policy allows scripts and
styles from Postulo's own origin only, so every one of these is copied into the tree and
committed. Three scripts do the copying and each carries the notice with the work:

- `npm run sync:icons` — the icons, from `lucide-static`, each with its `@license` line.
- `npm run sync:flags` — the flags, from `flag-icons`, with `LICENSE.txt`.
- `npm run sync:vendor` — htmx and zxcvbn, from their packages at the versions
  `package.json` pins, with their notices; and Basecoat's notice beside the stylesheet.

`npm run build:css` compiles the stylesheet, keeping Tailwind's banner and the Basecoat one.

Postulo's dependencies that are *not* in this tree — Django, WeasyPrint, the rest of
`pyproject.toml` — arrive through the package index at install time under their own
licences, and the container image's bill of materials lists them for a release.
