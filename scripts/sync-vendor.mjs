// Copy the third-party browser scripts Postulo serves itself from node_modules into the
// static directory. The result is committed, like the compiled stylesheet and the icons:
// Node is needed to change the set, never to run the application, and the content
// security policy allows scripts from Postulo's own origin only, so nothing is ever loaded
// from a CDN.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const DESTINATION = "src/postulo/static/js/vendor";

// The licence travels with the code, as `sync-flags.mjs` does for the artwork: MIT asks that
// the notice go wherever the files go, and a notice that stays in node_modules never leaves
// this machine (#279). [source in node_modules, destination from the repository root]. The
// three zxcvbn packages carry one identical text, so one copy stands beside the three files.
// Basecoat is compiled into the stylesheet rather than served as a file, so its notice sits
// beside the stylesheet and the banner in `assets/css/app.css` points at it. htmx is 0BSD,
// which asks for nothing, and the notice is copied all the same, so that no file in the
// tree is somebody else's work with nothing beside it saying so.
const NOTICES = [
  ["@zxcvbn-ts/core/LICENSE.txt", "src/postulo/static/js/vendor/zxcvbn/LICENSE.txt"],
  ["htmx.org/LICENSE", "src/postulo/static/js/vendor/htmx.LICENSE.txt"],
  ["basecoat-css/LICENSE.md", "src/postulo/static/css/basecoat.LICENSE.txt"],
];

// [source in node_modules, name under DESTINATION]
//
// Basecoat's component scripts (`basecoat-css/dist/js/*`) are not in this list on purpose
// (#262). Its dropdown menu wants a <button> trigger and hides the panel until the script
// runs, so a menu it drives has no path with scripts off; Postulo puts Basecoat's markup and
// stylesheet on a <details> the browser opens itself, and app.js adds the arrow keys. Its
// accordion is a <details> already and needs no script; its tabs would need the no-script
// shape settled first. Vendor one here the day a component genuinely needs it.
const FILES = [
  // htmx drives the partial updates. It arrived in the first commit with no record of
  // where from, and only the version string inside the bundle said which it was (#279);
  // pinned in package.json now, so a bump is a diff worth reading.
  ["htmx.org/dist/htmx.min.js", "htmx.min.js"],
  // zxcvbn estimates password strength in the browser, so a password never leaves it
  // before the person submits the form. Core plus the dictionaries it scores against.
  ["@zxcvbn-ts/core/dist/zxcvbn-ts.js", "zxcvbn/core.js"],
  ["@zxcvbn-ts/language-common/dist/zxcvbn-ts.js", "zxcvbn/language-common.js"],
  ["@zxcvbn-ts/language-en/dist/zxcvbn-ts.js", "zxcvbn/language-en.js"],
];

let failed = false;
for (const [source, name] of FILES) {
  const from = join("node_modules", source);
  if (!existsSync(from)) {
    console.error(`${from} is missing; run "npm ci" first.`);
    failed = true;
    continue;
  }
  const to = join(DESTINATION, name);
  mkdirSync(dirname(to), { recursive: true });
  // The bundles end with a pointer to a source map that is not shipped. Django's
  // manifest storage follows such references at collectstatic time and refuses a
  // file that points at nothing, so the pointer goes; the code is unchanged.
  const text = readFileSync(from, "utf8").replace(/^\/\/# sourceMappingURL=.*\n?$/m, "");
  writeFileSync(to, text);
  console.log(`${name} <- ${source}`);
}
for (const [source, to] of NOTICES) {
  const from = join("node_modules", source);
  if (!existsSync(from)) {
    console.error(`${from} is missing; run "npm ci" first.`);
    failed = true;
    continue;
  }
  mkdirSync(dirname(to), { recursive: true });
  // Verbatim but for the end of the file: a notice that does not end in a newline would be
  // rewritten by the end-of-file hook on every commit, and then differ from what this copies.
  writeFileSync(to, readFileSync(from, "utf8").replace(/\s*$/, "
"));
  console.log(`${to} <- ${source}`);
}
process.exit(failed ? 1 : 0);
