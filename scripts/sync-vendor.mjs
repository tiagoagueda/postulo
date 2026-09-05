// Copy the third-party browser scripts Postulo serves itself from node_modules into the
// static directory. The result is committed, like the compiled stylesheet and the icons:
// Node is needed to change the set, never to run the application, and the content
// security policy allows scripts from Postulo's own origin only, so nothing is ever loaded
// from a CDN.

import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";

const DESTINATION = "src/postulo/static/js/vendor";

// [source in node_modules, name under DESTINATION]
const FILES = [
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
  copyFileSync(from, to);
  console.log(`${name} <- ${source}`);
}
process.exit(failed ? 1 : 0);
