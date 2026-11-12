// /api/* routes. Only this list exists; every other path or method under /api answers a JSON
// 404 (an OPTIONS preflight too, so no other site can post here).
//
//   POST /api/v1/public/submissions
//   GET  /api/v1/public/submissions/{code}
//   GET  /api/v1/public/dashboard?dataset=public|synthetic
//   GET  /api/v1/public/status
//   GET  /api/v1/public/health
//   POST /api/v1/internal/evaluate          only when the secret EVAL_TOKEN is set

import { evaluate } from "./evaluate.js";
import { error, notFound } from "./http.js";
import { logEvent } from "./log.js";
import { dashboard, health, status } from "./reads.js";
import { lookup, submit } from "./submissions.js";

export const PREFIX = "/api/v1/public";
export const EVALUATE_PATH = "/api/v1/internal/evaluate";
const SUBMISSION = new RegExp(`^${PREFIX}/submissions/([^/]+)$`);

function route(request, env, ctx, deps) {
  const { pathname } = new URL(request.url);
  if (request.method === "POST") {
    if (pathname === `${PREFIX}/submissions`) return submit(request, env, ctx, deps);
    if (pathname === EVALUATE_PATH) return evaluate(request, env, deps);
    return null;
  }
  if (request.method !== "GET") return null;
  if (pathname === `${PREFIX}/status`) return status(env, deps);
  if (pathname === `${PREFIX}/dashboard`) return dashboard(request, env, deps);
  if (pathname === `${PREFIX}/health`) return health(env);
  const match = SUBMISSION.exec(pathname);
  return match ? lookup(match[1], env, deps) : null;
}

/** Answer an /api request. An unexpected failure is a JSON 500; only its error name is logged. */
export async function handleApi(request, env, ctx, deps) {
  try {
    return (await route(request, env, ctx, deps)) ?? notFound();
  } catch (err) {
    logEvent("api_error", { name: err?.name ?? "error" });
    return error(500, "internal");
  }
}
