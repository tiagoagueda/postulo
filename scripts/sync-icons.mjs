// Copy the icons listed in assets/icons.txt from the lucide-static package into the
// static directory, and remove anything there that is not listed. The result is
// committed, like the compiled stylesheet: Node is needed to change the set, never to
// run the application. Exits non-zero on a name Lucide does not have.

import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";

const LIST = "assets/icons.txt";
const SOURCE = "node_modules/lucide-static/icons";
const DESTINATION = "src/postulo/static/icons";

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
for (const name of wanted) {
  const from = join(SOURCE, `${name}.svg`);
  if (!existsSync(from)) {
    console.error(`Lucide has no icon named "${name}" (see https://lucide.dev/icons).`);
    failed = true;
    continue;
  }
  copyFileSync(from, join(DESTINATION, `${name}.svg`));
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
console.log(`${wanted.length} icons in ${DESTINATION}`);
