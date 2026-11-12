// Metrics of the Worker anonymizer (worker/anonymize/) on the planted personal data of the
// synthetic set, with the same definitions as src/enps/evaluation/anonymization.py, plus the CPU
// time per comment that matters for the Workers Free plan (10 ms of CPU per request).
//
//   node scripts/evaluate_worker_anonymizer.mjs [--responses data/synthetic/responses.csv]
//        [--gold data/synthetic/pii_gold.jsonl] [--out metrics/anonymization_worker.json]
//        [--python metrics/anonymization.json]
//
// A gold span is "caught" when every letter and digit in it is covered by the union of predicted
// spans of any type ("caught_same_type": predicted spans of the gold type only). A predicted span
// is a false positive when it overlaps no gold span. The leak rate is the share of gold strings
// still present verbatim (as whole words) in the anonymized text. Gold offsets count Unicode code
// points (Python); they are converted to JavaScript string indices before comparing.
//
// No dependencies. The anonymizer module is imported after the clock starts, so its load time
// (word lists included) is measured too.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MODULE_URL = new URL("../worker/anonymize/index.js", import.meta.url);
const WARMUP_PASSES = 3;
const ALNUM = /[\p{L}\p{N}]/u;
const WORD_CLASS = String.raw`[\p{L}\p{N}_]`;

// ------------------------------------------------------------------ files

/** RFC 4180 CSV with quoted fields, doubled quotes and line breaks inside quotes. */
export function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0;
  const endField = () => {
    row.push(field);
    field = "";
  };
  const endRow = () => {
    endField();
    rows.push(row);
    row = [];
  };
  while (i < text.length) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i += 2;
        continue;
      }
      if (ch === '"') quoted = false;
      else field += ch;
      i += 1;
      continue;
    }
    if (ch === '"' && field === "") quoted = true;
    else if (ch === ",") endField();
    else if (ch === "\n") endRow();
    else if (ch === "\r" && text[i + 1] === "\n") {
      endRow();
      i += 1;
    } else field += ch;
    i += 1;
  }
  if (field !== "" || row.length) endRow();
  const [header, ...body] = rows;
  return body.map((cells) => Object.fromEntries(header.map((name, k) => [name, cells[k] ?? ""])));
}

/** id -> comment, exactly as stored (gold offsets point into these strings). */
function loadResponses(path) {
  return new Map(parseCsv(readFileSync(path, "utf8")).map((row) => [row.id, row.comment ?? ""]));
}

function loadGold(path) {
  const gold = new Map();
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    const record = JSON.parse(line);
    gold.set(record.id, [...(gold.get(record.id) ?? []), ...(record.spans ?? [])]);
  }
  return gold;
}

/** Map from code point offsets (Python) to string indices (JavaScript) for one text. */
function codePointIndex(text) {
  const index = [0];
  let position = 0;
  for (const ch of text) {
    position += ch.length;
    index.push(position);
  }
  return index;
}

/** Gold spans in JavaScript coordinates, plus the spans whose text does not match the comment. */
function alignGold(gold, texts) {
  const aligned = new Map();
  const problems = [];
  for (const [docId, spans] of gold) {
    const comment = texts.get(docId);
    const index = comment === undefined ? null : codePointIndex(comment);
    const out = [];
    for (const g of spans) {
      const start = index?.[g.start];
      const end = index?.[g.end];
      if (comment === undefined || start === undefined || end === undefined || comment.slice(start, end) !== g.text) {
        problems.push(`${docId} ${g.type} ${JSON.stringify(g.text)}`);
      }
      out.push({ ...g, start: start ?? g.start, end: end ?? g.end });
    }
    aligned.set(docId, out);
  }
  return { aligned, problems };
}

// ------------------------------------------------------------------ metrics

function ratio(num, den) {
  return den ? Math.round((num / den) * 10000) / 10000 : null;
}

function covered(spans, type = null) {
  const set = new Set();
  for (const s of spans) {
    if (type !== null && s.type !== type) continue;
    for (let k = s.start; k < s.end; k += 1) set.add(k);
  }
  return set;
}

function isCaught(g, coveredSet) {
  let position = g.start;
  for (const ch of g.text) {
    if (ALNUM.test(ch) && !coveredSet.has(position)) return false;
    position += ch.length;
  }
  return true;
}

function escapeRegExp(value) {
  return value.replace(/[\\^$.*+?()[\]{}|/]/g, "\\$&");
}

function leaked(goldText, output) {
  return new RegExp(`(?<!${WORD_CLASS})${escapeRegExp(goldText)}(?!${WORD_CLASS})`, "u").test(output);
}

function overlapsGold(span, goldSpans) {
  return goldSpans.some((g) => span.start < g.end && g.start < span.end);
}

function counter() {
  return new Map();
}

function bump(stats, type, key, by = 1) {
  if (!stats.has(type)) stats.set(type, {});
  const row = stats.get(type);
  row[key] = (row[key] ?? 0) + by;
}

function goldRow(s) {
  const gold = s.gold ?? 0;
  const caught = s.caught ?? 0;
  const typed = s.caught_same_type ?? 0;
  const leakedCount = s.leaked ?? 0;
  return {
    gold,
    caught,
    caught_same_type: typed,
    recall: ratio(caught, gold),
    recall_typed: ratio(typed, gold),
    leaked: leakedCount,
    leak_rate: ratio(leakedCount, gold),
  };
}

function predRow(s) {
  const predicted = s.predicted ?? 0;
  const fp = s.false_positives ?? 0;
  return { predicted, false_positives: fp, precision: ratio(predicted - fp, predicted) };
}

function sum(rows) {
  const total = {};
  for (const row of rows) for (const [key, value] of Object.entries(row)) total[key] = (total[key] ?? 0) + value;
  return total;
}

function sortedObject(stats, rowFn) {
  return Object.fromEntries([...stats.keys()].sort().map((type) => [type, rowFn(stats.get(type))]));
}

/**
 * Recall per gold type, precision per predicted type, leak rate and error lists.
 * gold: id -> gold spans in JavaScript coordinates. results: id -> anonymizer output for every
 * evaluated comment. texts: id -> original comment.
 */
export function evaluate(gold, results, texts) {
  const goldStats = counter();
  const predStats = counter();
  const misses = [];
  const mismatches = [];
  const leaks = [];
  const falsePositives = [];

  for (const [docId, goldSpans] of gold) {
    const result = results.get(docId);
    const spans = result ? result.spans : [];
    const output = result ? result.text : null;
    const coveredAny = covered(spans);
    for (const g of goldSpans) {
      bump(goldStats, g.type, "gold");
      const caught = isCaught(g, coveredAny);
      const caughtTyped = caught && isCaught(g, covered(spans, g.type));
      bump(goldStats, g.type, "caught", Number(caught));
      bump(goldStats, g.type, "caught_same_type", Number(caughtTyped));
      const row = { id: docId, type: g.type, text: g.text };
      if (!caught) misses.push(row);
      else if (!caughtTyped) {
        const predicted = [...new Set(spans.filter((s) => s.start < g.end && g.start < s.end).map((s) => s.type))].sort();
        mismatches.push({ ...row, predicted });
      }
      if (output === null || leaked(g.text, output)) {
        bump(goldStats, g.type, "leaked");
        leaks.push(row);
      }
    }
  }

  for (const [docId, result] of results) {
    const goldSpans = gold.get(docId) ?? [];
    for (const span of result.spans) {
      bump(predStats, span.type, "predicted");
      if (!overlapsGold(span, goldSpans)) {
        bump(predStats, span.type, "false_positives");
        const text = texts.get(docId)?.slice(span.start, span.end) ?? null;
        falsePositives.push({ id: docId, type: span.type, text, source: span.source });
      }
    }
  }

  return {
    documents: results.size,
    documents_with_gold: [...gold.values()].filter((spans) => spans.length).length,
    overall: { ...goldRow(sum(goldStats.values())), ...predRow(sum(predStats.values())) },
    by_gold_type: sortedObject(goldStats, goldRow),
    by_predicted_type: sortedObject(predStats, predRow),
    misses,
    type_mismatches: mismatches,
    leaks,
    false_positives: falsePositives,
  };
}

// ------------------------------------------------------------------ timing

function cpuMs(since) {
  const { user, system } = process.cpuUsage(since);
  return (user + system) / 1000;
}

function round(value, digits = 3) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function summary(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const at = (q) => sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)];
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  return { mean: round(mean), p50: round(at(0.5)), p95: round(at(0.95)), max: round(sorted.at(-1)) };
}

/** One call per comment, timed with process.cpuUsage (user + system) and performance.now. */
function timedPass(anonymize, texts) {
  const cpu = [];
  const wall = [];
  const results = new Map();
  for (const [docId, text] of texts) {
    const cpuStart = process.cpuUsage();
    const wallStart = performance.now();
    results.set(docId, anonymize(text));
    wall.push(performance.now() - wallStart);
    cpu.push(cpuMs(cpuStart));
  }
  return { cpu, wall, results };
}

async function loadAnonymizer() {
  const cpuStart = process.cpuUsage();
  const wallStart = performance.now();
  const module = await import(MODULE_URL);
  return {
    module,
    loadMs: round(performance.now() - wallStart),
    loadCpuMs: round(cpuMs(cpuStart)),
  };
}

// ------------------------------------------------------------------ report

function fmt(value) {
  return value === null || value === undefined ? "-" : String(value);
}

function fmtRatio(value) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

function formatTable(metrics) {
  const types = [...new Set([...Object.keys(metrics.by_gold_type), ...Object.keys(metrics.by_predicted_type)])].sort();
  const head = ["type".padEnd(11), "gold", "caught", "recall", "typed", "leak", "pred", "FP", "prec"];
  const widths = [11, 6, 8, 8, 8, 7, 7, 5, 7];
  const line = (cells) => cells.map((c, k) => (k === 0 ? String(c).padEnd(widths[k]) : String(c).padStart(widths[k]))).join("");
  const rows = types.map((t) => [t, metrics.by_gold_type[t] ?? {}, metrics.by_predicted_type[t] ?? {}]);
  rows.push(["ALL", metrics.overall, metrics.overall]);
  return [line(head), ...rows.map(([name, g, p]) => line([
    name, fmt(g.gold ?? 0), fmt(g.caught ?? 0), fmtRatio(g.recall), fmtRatio(g.recall_typed),
    fmtRatio(g.leak_rate), fmt(p.predicted ?? 0), fmt(p.false_positives ?? 0), fmtRatio(p.precision),
  ]))].join("\n");
}

function formatComparison(worker, python) {
  const types = [...new Set([
    ...Object.keys(worker.by_gold_type), ...Object.keys(python.by_gold_type),
    ...Object.keys(worker.by_predicted_type), ...Object.keys(python.by_predicted_type),
  ])].sort();
  const cells = (m, t) => {
    const g = t === "ALL" ? m.overall : m.by_gold_type[t] ?? {};
    const p = t === "ALL" ? m.overall : m.by_predicted_type[t] ?? {};
    return [fmtRatio(g.recall), fmtRatio(g.leak_rate), fmt(p.false_positives ?? 0), fmtRatio(p.precision)];
  };
  const widths = [11, 6, 9, 9, 9, 9, 7, 7, 9, 9];
  const line = (c) => c.map((x, k) => (k === 0 ? String(x).padEnd(widths[k]) : String(x).padStart(widths[k]))).join("");
  const out = [line(["type", "gold", "rec py", "rec js", "leak py", "leak js", "FP py", "FP js", "prec py", "prec js"])];
  for (const t of [...types, "ALL"]) {
    const gold = t === "ALL" ? worker.overall.gold : worker.by_gold_type[t]?.gold ?? 0;
    const [rp, lp, fp, pp] = cells(python, t);
    const [rw, lw, fw, pw] = cells(worker, t);
    out.push(line([t, gold, rp, rw, lp, lw, fp, fw, pp, pw]));
  }
  return out.join("\n");
}

function parseArgs(argv) {
  const args = {
    responses: "data/synthetic/responses.csv",
    gold: "data/synthetic/pii_gold.jsonl",
    out: "metrics/anonymization_worker.json",
    python: "metrics/anonymization.json",
  };
  for (let k = 0; k < argv.length; k += 2) {
    const key = argv[k].replace(/^--/, "");
    if (!(key in args) || argv[k + 1] === undefined) throw new Error(`unknown or incomplete option ${argv[k]}`);
    args[key] = argv[k + 1];
  }
  return Object.fromEntries(Object.entries(args).map(([key, value]) => [key, resolve(ROOT, value)]));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { module, loadMs, loadCpuMs } = await loadAnonymizer();
  const { anonymize, ANONYMIZER_VERSION } = module;

  const texts = new Map([...loadResponses(args.responses)].filter(([, text]) => text.trim()));
  const { aligned: gold, problems } = alignGold(loadGold(args.gold), texts);

  const [firstId, firstText] = texts.entries().next().value;
  const firstCpuStart = process.cpuUsage();
  const firstWallStart = performance.now();
  anonymize(firstText);
  const firstCall = { id: firstId, wall_ms: round(performance.now() - firstWallStart), cpu_ms: round(cpuMs(firstCpuStart)) };

  const cold = timedPass(anonymize, texts);
  for (let pass = 1; pass < WARMUP_PASSES; pass += 1) timedPass(anonymize, texts);
  const warm = timedPass(anonymize, texts);

  const metrics = evaluate(gold, warm.results, texts);
  const ids = [...texts.keys()];
  const slowest = warm.cpu.indexOf(Math.max(...warm.cpu));
  metrics.anonymizer_version = ANONYMIZER_VERSION;
  metrics.gold_offset_problems = problems;
  metrics.runtime = {
    node: process.version,
    cpu_clock: "process.cpuUsage, user + system time of the whole process, one call per comment",
    module_load_ms: loadMs,
    module_load_cpu_ms: loadCpuMs,
    first_call: firstCall,
    comments: texts.size,
    warmup_passes: WARMUP_PASSES,
    cold_pass_cpu_ms_per_comment: summary(cold.cpu),
    cpu_ms_per_comment: summary(warm.cpu),
    wall_ms_per_comment: summary(warm.wall),
    slowest: { id: ids[slowest], chars: texts.get(ids[slowest]).length, cpu_ms: round(warm.cpu[slowest]) },
    anonymize_s: round(warm.wall.reduce((a, b) => a + b, 0) / 1000),
    ms_per_comment: round(warm.wall.reduce((a, b) => a + b, 0) / warm.wall.length),
  };

  mkdirSync(dirname(args.out), { recursive: true });
  writeFileSync(args.out, `${JSON.stringify(metrics, null, 2)}\n`, "utf8");

  console.log(formatTable(metrics));
  const rt = metrics.runtime;
  console.log(
    `documents ${metrics.documents}, with planted data ${metrics.documents_with_gold}; `
    + `module load ${rt.module_load_ms} ms; CPU per comment mean ${rt.cpu_ms_per_comment.mean} ms, `
    + `p95 ${rt.cpu_ms_per_comment.p95} ms, max ${rt.cpu_ms_per_comment.max} ms -> ${relative(ROOT, args.out)}`,
  );
  if (problems.length) console.log(`warning: ${problems.length} gold spans do not match the comments`);
  if (existsSync(args.python)) {
    console.log(`\nPython anonymizer (${relative(ROOT, args.python)}) vs Worker anonymizer:`);
    console.log(formatComparison(metrics, JSON.parse(readFileSync(args.python, "utf8"))));
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
