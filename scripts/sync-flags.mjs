// Copy the flags listed in assets/flags.txt from the flag-icons package into the static
// directory, and remove anything there that is not listed. The result is committed, like
// the compiled stylesheet and the icons: Node is needed to change the set, never to run
// the application.
//
// Unlike the icons these are served as <img src>, not inlined. Each file is its own
// document that way -- flag-icons gives every flag an `id`, and two dozen of them inlined
// into one page would be two dozen duplicate ids -- and the browser caches each one under
// a content-hashed name instead of re-downloading it inside every page's HTML. Spain's
// flag is 79 KB of coat of arms; nobody should pay for that twice.
//
// The licence travels with the artwork. flag-icons is MIT, which asks that the notice go
// wherever the files go, so it is copied in beside them rather than only mentioned in the
// README.

import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";

const LIST = "assets/flags.txt";
const PACKAGE = "node_modules/flag-icons";
const SOURCE = join(PACKAGE, "flags/4x3");
const DESTINATION = "src/postulo/static/flags";
const NOTICE = "LICENSE.txt";

const wanted = readFileSync(LIST, "utf8")
  .split(/\r?\n/)
  .map((line) => line.replace(/#.*/, "").trim())
  .filter(Boolean);

if (!existsSync(SOURCE)) {
  console.error(`${SOURCE} is missing; run "npm ci" first.`);
  process.exit(1);
}

mkdirSync(DESTINATION, { recursive: true });

let failed = false;
for (const code of wanted) {
  if (!/^[a-z]{2}$/.test(code)) {
    console.error(`"${code}" is not an ISO 3166-1 alpha-2 code in lower case.`);
    failed = true;
    continue;
  }
  const from = join(SOURCE, `${code}.svg`);
  if (!existsSync(from)) {
    console.error(`flag-icons has no flag for "${code}".`);
    failed = true;
    continue;
  }
  copyFileSync(from, join(DESTINATION, `${code}.svg`));
}

copyFileSync(join(PACKAGE, "LICENSE"), join(DESTINATION, NOTICE));

const keep = new Set([...wanted.map((code) => `${code}.svg`), NOTICE]);
for (const file of readdirSync(DESTINATION)) {
  if (!keep.has(file)) {
    rmSync(join(DESTINATION, file));
    console.log(`removed ${file}: not in ${LIST}`);
  }
}

if (failed) {
  process.exit(1);
}
console.log(`${wanted.length} flags in ${DESTINATION}`);
