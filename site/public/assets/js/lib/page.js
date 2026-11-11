// Page data embedded by the build in <script type="application/json" id="page-data">.
import { createT } from "./i18n.js";

/**
 * @returns {{lang: string, page: string, strings: object, routes: object, anchors: object,
 *   query: object, vars: object, config: string, taxonomy: string, t: Function}}
 */
export function readPageData(doc = document) {
  const node = doc.getElementById("page-data");
  const data = node ? JSON.parse(node.textContent) : { lang: doc.documentElement.lang.slice(0, 2), strings: {} };
  data.t = createT(data.strings || {}, data.vars || {});
  return data;
}
