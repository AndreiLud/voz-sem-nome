// PT | EN toggle: links to the equivalent page and keeps the query string and the hash,
// translating dashboard parameters (?base=sintetica <-> ?dataset=synthetic) and landing anchors.

export const URL_CHANGE_EVENT = "vsn:urlchange";

function reverseValue(values, lang, value) {
  return Object.keys(values).find((key) => values[key][lang] === value);
}

/** Pure: translate a query string and a hash from one language to the other. */
export function translateUrlParts(search, hash, from, to, maps) {
  const params = new URLSearchParams(search);
  const out = new URLSearchParams();
  for (const [name, value] of params) {
    const entry = Object.values(maps.query || {}).find((q) => q.param && q.param[from] === name);
    if (!entry) {
      out.append(name, value);
      continue;
    }
    const key = entry.values ? reverseValue(entry.values, from, value) : undefined;
    out.append(entry.param[to], key ? entry.values[key][to] : value);
  }
  let newHash = hash;
  const id = hash.replace(/^#/, "");
  if (id) {
    const key = Object.keys(maps.anchors || {}).find((k) => maps.anchors[k][from] === id);
    if (key) newHash = `#${maps.anchors[key][to]}`;
  }
  const query = out.toString();
  return { search: query ? `?${query}` : "", hash: newHash };
}

export function initLangToggle(page, win = window) {
  const link = win.document.getElementById("lang-toggle");
  if (!link) return;
  const base = new URL(link.getAttribute("href"), win.location.href).pathname;
  const to = link.dataset.lang;
  const update = () => {
    const { search, hash } = translateUrlParts(win.location.search, win.location.hash, page.lang, to, page);
    link.setAttribute("href", base + search + hash);
  };
  update();
  win.addEventListener("hashchange", update);
  win.addEventListener("popstate", update);
  win.addEventListener(URL_CHANGE_EVENT, update);
}

/** Page scripts call this after history.replaceState/pushState so the toggle follows. */
export function notifyUrlChange(win = window) {
  win.dispatchEvent(new Event(URL_CHANGE_EVENT));
}
