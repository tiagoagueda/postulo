// Copy the marks listed in assets/brands.txt from the simple-icons package into the
// static directory, and write the notice beside them (#654). Like the icons, the result
// is committed: Node is needed to change the set, never to run the application.
//
// Exits non-zero on a slug the package no longer has (an upstream removal is a decision at
// the bump, never silent), and on a mark whose own licence entry is outside
// assets/brand-terms.txt.
//
// Each mark is copied unmodified except for one thing: Simple Icons ships it with no fill,
// so a fill is written onto the root. A mark has one of two modes (TRADEMARKS.md rule 2),
// recorded in NOTICE.txt:
//
//   brand          its owner's published colour, written as the fill (the default).
//   single-colour  `currentColor`, which the stylesheet makes black on the light page and
//                  white on the dark one. Only for a mark whose line in assets/brands.txt
//                  records the owner's guidelines page and what it says that allows a
//                  one-colour version; without both, this script refuses the mark.

import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const LIST = "assets/brands.txt";
const LICENCES = "assets/brand-terms.txt";
const PACKAGE = "node_modules/simple-icons";
const DESTINATION = "src/postulo/static/brands";

// A comment starts at a `#` that begins the line or follows a space, so a URL's fragment
// is not one.
const lines = (path) =>
  readFileSync(path, "utf8")
    .split(/\r?\n/)
    .map((line) => line.replace(/(^|\s)#.*/, "").trim())
    .filter(Boolean);

const MODES = ["brand", "single-colour"];

// A line is `slug`, or `slug single-colour <guidelines URL> <what they say>`.
let failed = false;
const parsed = new Map();
for (const line of lines(LIST)) {
  const [, slug, mode = "brand", url = "", evidence = ""] = line.match(/^(\S+)(?:\s+(\S+)(?:\s+(\S+)(?:\s+(.*))?)?)?$/);
  if (!MODES.includes(mode)) {
    console.error(`"${slug}" has the mode "${mode}"; the modes are ${MODES.join(" and ")}.`);
    failed = true;
  } else if (mode === "single-colour" && (!/^https:\/\/\S+$/.test(url) || !evidence.trim())) {
    console.error(
      `"${slug}" is single-colour, which needs the owner's guidelines page (https) and what it says allowing a one-colour version, after the mode in ${LIST}.`,
    );
    failed = true;
  } else {
    parsed.set(slug, { mode, url, evidence: evidence.trim() });
  }
}
if (failed) {
  process.exit(1);
}

const wanted = [...parsed.keys()];
const allowed = new Set(lines(LICENCES));

if (!existsSync(PACKAGE)) {
  console.error(`${PACKAGE} is missing; run "npm ci" first.`);
  process.exit(1);
}

const version = JSON.parse(readFileSync(join(PACKAGE, "package.json"), "utf8")).version;
const data = new Map(
  JSON.parse(readFileSync(join(PACKAGE, "data", "simple-icons.json"), "utf8")).map((entry) => [entry.slug, entry]),
);

mkdirSync(DESTINATION, { recursive: true });

const notices = [];
for (const slug of [...wanted].sort()) {
  const entry = data.get(slug);
  const from = join(PACKAGE, "icons", `${slug}.svg`);
  if (!entry || !existsSync(from)) {
    console.error(`simple-icons ${version} has no mark named "${slug}": removed upstream? Read the removals, then drop it from ${LIST}.`);
    failed = true;
    continue;
  }
  const licence = entry.license ? entry.license.type : "";
  if (licence && !allowed.has(licence)) {
    console.error(`"${slug}" carries the licence "${licence}", which is not in ${LICENCES}.`);
    failed = true;
    continue;
  }
  const { mode, url, evidence } = parsed.get(slug);
  const colour = `#${entry.hex.toUpperCase()}`;
  const fill = mode === "brand" ? colour : "currentColor";
  // One trailing newline, as the repository's end-of-file hook leaves every file.
  const svg = readFileSync(from, "utf8")
    .replace(/^<svg\b/, `<svg fill="${fill}"`)
    .replace(/\s*$/, "\n");
  writeFileSync(join(DESTINATION, `${slug}.svg`), svg);
  notices.push(
    [
      `${slug}`,
      `  Owner: ${entry.title}`,
      mode === "brand"
        ? `  Mode: brand (the published colour ${colour}, unmodified; it clears 3:1 on the light and the dark page)`
        : "  Mode: single-colour (currentColor, black on the light page and white on the dark one; its owner's guidelines allow a one-colour version)",
      `  Licence: ${licence || "none declared for the artwork; the mark itself is its owner's"}`,
      `  Source: ${entry.source || "not stated"}`,
      `  Guidelines: ${entry.guidelines || "not stated"}`,
      ...(mode === "single-colour" ? [`  Allowed by: ${url}`, `  They say: ${evidence}`] : []),
    ].join("\n"),
  );
}

for (const file of readdirSync(DESTINATION)) {
  if (file.endsWith(".svg") && !wanted.includes(file.slice(0, -4))) {
    rmSync(join(DESTINATION, file));
    console.log(`removed ${file}: not in ${LIST}`);
  }
}

if (failed) {
  process.exit(1);
}

const header = `Brand marks in this directory
=============================

Each file is a logo that belongs to the owner named below, taken from the Simple Icons
package (simple-icons ${version}, https://simpleicons.org, whose own artwork notice is
CC0-1.0). Postulo shows a mark only to identify the service it stands for, and claims no
endorsement by, or involvement of, its owner: the owners are not involved with Postulo.

These files are NOT covered by Postulo's AGPL-3.0-or-later licence, which cannot speak for
somebody else's trademark. They are used unmodified in the one mode recorded for each mark
(its published colour, or one colour where its owner's guidelines allow that, and the page
that says so is recorded), under the rule in TRADEMARKS.md. If you own one of these marks and
would rather it were not used, write to tiago.agueda@tiagoagueda.com and it will be changed.

Where a mark carries a licence of its own, it is named; CC-BY-4.0 and CC-BY-SA-4.0 ask for
attribution, which is this notice, and the second asks that the artwork stay under the same
terms.

Generated by scripts/sync-brands.mjs from assets/brands.txt. Do not edit by hand.

`;
writeFileSync(join(DESTINATION, "NOTICE.txt"), header + notices.join("\n\n") + "\n");
console.log(`${wanted.length} brand marks in ${DESTINATION}`);
