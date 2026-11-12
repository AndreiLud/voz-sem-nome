// Entry point for `node --test worker/test/`: Node resolves a folder argument to this file,
// which loads every *.test.js file of the folder, so new test files are never left out.
import { readdirSync } from "node:fs";

const here = new URL(".", import.meta.url);
for (const file of readdirSync(here).filter((name) => name.endsWith(".test.js")).sort()) {
  await import(new URL(file, here).href);
}
