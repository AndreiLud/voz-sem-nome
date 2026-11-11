// Entry module of every page: language toggle, copy buttons, then the page's own module.
import { readPageData } from "./lib/page.js";
import { initLangToggle } from "./lib/lang.js";
import { initCopyButtons } from "./lib/copy.js";
import { ensureLiveRegion } from "./lib/live.js";

const PAGE_MODULES = {
  home: () => import("./home.js"),
  dashboard: () => import("./dashboard.js"),
  submit: () => import("./submit.js"),
};

async function boot() {
  const page = readPageData();
  ensureLiveRegion();
  initLangToggle(page);
  initCopyButtons(page);
  const load = PAGE_MODULES[page.page];
  if (load) {
    const mod = await load();
    await mod.init(page);
  }
}

boot().catch((error) => {
  console.error(error);
});
