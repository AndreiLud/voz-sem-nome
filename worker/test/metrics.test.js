import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { buildDashboard, pipelineInfo, visibleAreaCodes } from "../metrics/aggregates.js";
import { DASHBOARD_CONFIG, PROFANITY } from "../metrics/data.js";
import { category, summarize } from "../metrics/enps.js";
import { maskProfanity, publicExcerpt, truncate } from "../metrics/excerpt.js";
import { pyRound, pySum, roundHalfAway } from "../metrics/numbers.js";
import { percentile, productionStats } from "../metrics/production.js";
import { cellKey, partitionGroups, suppressCells } from "../metrics/suppression.js";
import { parseIso, spDay, toIso } from "../metrics/time.js";

// Cases written by scripts/build_dashboard_fixtures.py with the Python metrics
// (tests/test_dashboard_cases.py keeps the file current).
const FIXTURE = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/dashboard_cases.json", import.meta.url), "utf8"),
);
const { areas: AREAS, tenures: TENURES } = DASHBOARD_CONFIG;

const toRows = (c) => c.rows.map((values) => Object.fromEntries(FIXTURE.columns.map((col, i) => [col, values[i]])));
const withoutGeneratedAt = ({ generated_at: _ignored, ...rest }) => rest;
const sum = (values) => [...values].reduce((a, b) => a + b, 0);

/** Rows of SELECT area, count(*) AS n FROM submissions GROUP BY area (SQLite sorts by area). */
function areaCounts(rows) {
  const counts = new Map();
  for (const { area } of rows) counts.set(area, (counts.get(area) ?? 0) + 1);
  return [...counts].sort(([a], [b]) => (a < b ? -1 : 1)).map(([area, n]) => ({ area, n }));
}

/** Seeded generator (mulberry32), so the invariant tests are reproducible. */
function seeded(seed) {
  let state = seed;
  const next = () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const int = (lo, hi) => lo + Math.floor(next() * (hi - lo + 1));
  return { next, int, choice: (items) => items[int(0, items.length - 1)] };
}

describe("parity with the Python metrics (tests/fixtures/dashboard_cases.json)", () => {
  describe("dashboard", () => {
    for (const c of FIXTURE.dashboards) {
      it(c.name, () => {
        const rows = toRows(c);
        const actual = buildDashboard(rows, { dataset: c.dataset, now: FIXTURE.now, cfg: c.cfg });
        assert.deepEqual(withoutGeneratedAt(actual), withoutGeneratedAt(c.expected));
        // deepEqual ignores key order; the serialized JSON must match the Python one too.
        assert.equal(JSON.stringify(withoutGeneratedAt(actual)), JSON.stringify(withoutGeneratedAt(c.expected)));
        assert.deepEqual([...visibleAreaCodes(areaCounts(rows), { cfg: c.cfg })].sort(), c.visible_areas);
      });
    }
  });

  describe("production statistics", () => {
    for (const c of FIXTURE.production) {
      it(c.name, () => assert.equal(JSON.stringify(productionStats(c)), JSON.stringify(c.expected)));
    }
  });

  it("rounds like Python round(x, 1)", () => {
    for (const [x, expected] of FIXTURE.rounding) assert.equal(pyRound(x, 1), expected, `round(${x}, 1)`);
  });

  it("sums floats like Python sum()", () => {
    for (const [values, expected] of FIXTURE.sums) assert.equal(pySum(values), expected, JSON.stringify(values));
  });

  it("masks profanity like Python", () => {
    for (const [text, expected] of FIXTURE.masks) assert.equal(maskProfanity(text), expected, JSON.stringify(text));
  });

  it("builds excerpts like Python", () => {
    for (const [text, limit, expected] of FIXTURE.excerpts) {
      assert.equal(publicExcerpt(text, limit), expected, `${JSON.stringify(text.slice(0, 40))} at ${limit}`);
    }
  });

  it("covers the cases the port must get right", () => {
    const names = FIXTURE.dashboards.map((c) => c.name).join("\n");
    for (const part of ["empty dataset", "exactly at k", "all promoters", "hidden areas", "more than 100"]) {
      assert.ok(names.includes(part), part);
    }
    assert.ok(FIXTURE.dashboards.some((c) => c.expected.critical.cases.some((x) => x.excerpt.includes("[ofensivo]"))));
    assert.ok(FIXTURE.rounding.length > 500 && FIXTURE.masks.length > 10 && FIXTURE.production.length >= 5);
  });
});

describe("eNPS", () => {
  it("rounds halves away from zero and never returns -0", () => {
    assert.equal(summarize([10, 7, 7, 7, 7, 7, 7, 7]).enps, 13);
    assert.equal(summarize([0, 7, 7, 7, 7, 7, 7, 7]).enps, -13);
    assert.equal(summarize([10, 10, 3]).enps, 33);
    assert.equal(roundHalfAway(-100, 250), 0);
    assert.ok(Object.is(roundHalfAway(-100, 250), 0));
  });

  it("handles all promoters, all detractors and an empty group", () => {
    assert.deepEqual(summarize([9, 10, 10]), {
      n: 3, enps: 100, promoters: 3, passives: 0, detractors: 0,
      pct_promoters: 100, pct_passives: 0, pct_detractors: 0,
    });
    assert.equal(summarize([0, 6, 3]).enps, -100);
    const empty = summarize([]);
    assert.ok(empty.n === 0 && empty.enps === null && empty.pct_detractors === null);
    assert.deepEqual([0, 6, 7, 8, 9, 10].map(category),
      ["detractor", "detractor", "passive", "passive", "promoter", "promoter"]);
    assert.throws(() => category(11), RangeError);
  });

  it("rounds shares half to even and sums floats like Python 3.12", () => {
    assert.equal(pyRound(6.25, 1), 6.2);
    assert.equal(pyRound(18.75, 1), 18.8);
    assert.equal(pyRound(0.35, 1), 0.3); // 0.35 is slightly below .35 in binary
    assert.equal(pySum(Array(10).fill(0.1)), 1);
  });
});

describe("minimum group rule", () => {
  it("merges a small group with the smallest visible one", () => {
    const part = partitionGroups(new Map([["a", 3], ["b", 10], ["c", 6], ["d", 0]]), 5);
    assert.deepEqual([part.visible, part.merged, part.otherN], [["b"], ["a", "c"], 9]);
  });

  it("hides everything when the total is below k", () => {
    const part = partitionGroups(new Map([["a", 2], ["b", 1]]), 5);
    assert.deepEqual([part.visible, part.otherN, part.otherShown], [[], 3, false]);
    assert.throws(() => partitionGroups(new Map(), 0), RangeError);
  });

  for (const seed of [0, 1, 2, 3]) {
    it(`never reveals one hidden group by subtraction (seed ${seed})`, () => {
      const rng = seeded(seed);
      const sizes = [0, 0, 1, 2, 3, 4, 5, 6, 7, 9, 12, 30];
      for (let round = 0; round < 2500; round += 1) {
        const k = rng.choice([2, 3, 5, 8]);
        const counts = new Map(Array.from({ length: rng.int(1, 9) }, (_, i) => [`g${i}`, rng.choice(sizes)]));
        const part = partitionGroups(counts, k);
        const total = sum(counts.values());
        const hidden = [...counts.keys()].filter((c) => !part.visible.includes(c) && counts.get(c) > 0);
        assert.ok(part.visible.every((c) => counts.get(c) >= k));
        assert.equal(total - sum(part.visible.map((c) => counts.get(c))), part.otherN);
        assert.deepEqual([...hidden].sort(), [...part.merged].sort());
        if (total >= k) {
          assert.notEqual(hidden.length, 1); // total minus visible groups is never one hidden group
          assert.ok(part.otherN === 0 || part.otherN >= k);
        } else {
          assert.deepEqual(part.visible, []);
          assert.equal(part.otherShown, false);
        }
      }
    });

    it(`leaves no line of the cross table with one suppressed cell (seed ${seed})`, () => {
      const rng = seeded(100 + seed);
      const sizes = [0, 0, 1, 2, 3, 4, 5, 6, 8, 15];
      for (let round = 0; round < 600; round += 1) {
        const k = rng.choice([3, 5]);
        const counts = new Map();
        for (const a of AREAS) for (const t of TENURES) counts.set(cellKey(a, t), rng.choice(sizes));
        const size = (a, t) => counts.get(cellKey(a, t));
        const areas = partitionGroups(new Map(AREAS.map((a) => [a, sum(TENURES.map((t) => size(a, t)))])), k);
        const tenures = partitionGroups(new Map(TENURES.map((t) => [t, sum(AREAS.map((a) => size(a, t)))])), k);
        const suppressed = suppressCells(counts, AREAS, TENURES, k, {
          hiddenRows: AREAS.filter((a) => !areas.visible.includes(a)),
          hiddenCols: TENURES.filter((t) => !tenures.visible.includes(t)),
        });
        const lines = [...AREAS.map((a) => TENURES.map((t) => cellKey(a, t))),
          ...TENURES.map((t) => AREAS.map((a) => cellKey(a, t)))];
        for (const line of lines) assert.notEqual(line.filter((c) => suppressed.has(c)).length, 1);
        for (const a of AREAS) {
          for (const t of TENURES) {
            if (!suppressed.has(cellKey(a, t))) {
              assert.ok(size(a, t) >= k && areas.visible.includes(a) && tenures.visible.includes(t));
            }
          }
        }
      }
    });
  }

  it("keeps the dashboard safe from the subtraction attack", () => {
    const rng = seeded(7);
    for (let round = 0; round < 300; round += 1) {
      const rows = Array.from({ length: rng.int(0, 80) }, () => ({
        public_id: String(rng.int(0, 1e9)), created_at: "2026-11-10T12:00:00.000Z", score: rng.int(0, 10),
        area: rng.choice(AREAS.slice(0, 5)), tenure: rng.choice(TENURES), comment_anon: "", status: "skipped",
      }));
      const data = buildDashboard(rows, { now: FIXTURE.now });
      const total = data.totals.responses;
      if (total === null) {
        assert.ok(rows.length < 5 && data.by_area.length === 0 && data.other_areas === null);
        continue;
      }
      for (const [listed, other, key] of [[data.by_area, data.other_areas, "area"], [data.by_tenure, data.other_tenures, "tenure"]]) {
        const otherN = other ? other.n : 0;
        assert.equal(total - sum(listed.map((g) => g.n)), otherN);
        assert.ok(otherN === 0 || otherN >= 5);
        const shown = new Set(listed.map((g) => g.code));
        assert.notEqual(new Set(rows.map((r) => r[key]).filter((code) => !shown.has(code))).size, 1);
      }
    }
  });
});

describe("D1 rows", () => {
  const base = { created_at: "2026-11-10T12:00:00.000Z", tenure: "lt1", comment_anon: "algo", sentiment: "neg" };

  it("accepts parsed arrays and treats queued_quota and failed rows as not classified", () => {
    const rows = [
      ...Array.from({ length: 5 }, (_, i) => ({ ...base, public_id: `p${i}`, score: 9, area: "operacoes", status: "done",
        themes: ["lid"], critical: 1, critical_types: ["am"], finished_at: "2026-11-10T12:00:05.000Z", model: "m" })),
      { ...base, public_id: "q", score: 1, area: "operacoes", status: "queued_quota", themes: null },
      { ...base, public_id: "f", score: 1, area: "operacoes", status: "failed", themes: "[\"lid\"]", critical: 1, critical_types: "[\"am\"]" },
    ];
    const data = buildDashboard(rows, { now: FIXTURE.now });
    assert.equal(data.totals.responses, 7);
    assert.equal(data.critical.total, 5);
    assert.equal(data.themes.find((t) => t.code === "lid").n, 5);
    assert.deepEqual(data.pipeline, { model: "m", quantization: null, prompt_version: null, anonymizer_version: null });
  });

  it("exposes the port through worker/metrics/index.js", async () => {
    const metrics = await import("../metrics/index.js");
    assert.equal(metrics.buildDashboard, buildDashboard);
    assert.equal(metrics.productionStats, productionStats);
    assert.equal(metrics.visibleAreaCodes, visibleAreaCodes);
  });

  it("reports an empty pipeline without classified rows", () => {
    assert.deepEqual(pipelineInfo([]), { model: null, quantization: null, prompt_version: null, anonymizer_version: null });
  });

  it("writes generated_at like timeutil.to_iso", () => {
    assert.equal(buildDashboard([], { now: FIXTURE.now }).generated_at, FIXTURE.dashboards[0].expected.generated_at);
    assert.equal(toIso(new Date(Date.UTC(2026, 10, 11, 13, 5, 0, 123))), "2026-11-11T13:05:00.123+00:00");
  });
});

describe("time", () => {
  it("uses Sao Paulo calendar days and reads naive timestamps as UTC", () => {
    assert.equal(spDay("2026-11-10T02:59:59.999Z"), "2026-11-09");
    assert.equal(spDay("2026-11-10T03:00:00.000+00:00"), "2026-11-10");
    assert.equal(spDay("2026-11-10T02:00:00"), "2026-11-09");
    assert.equal(parseIso("2026-11-10 03:00:00.123456"), Date.UTC(2026, 10, 10, 3, 0, 0, 123));
    assert.throws(() => parseIso("yesterday"), RangeError);
  });
});

describe("excerpts", () => {
  it("counts code points, so an emoji is one character", () => {
    const excerpt = publicExcerpt("\u{1F629}".repeat(10), 5);
    assert.equal([...excerpt].length, 5);
    assert.ok(excerpt.endsWith("…"));
    assert.equal(truncate("curto", 50), "curto");
  });

  it("loads the profanity list", () => {
    assert.ok(PROFANITY.includes("foda-se") && PROFANITY.indexOf("foda-se") < PROFANITY.indexOf("foda"));
    assert.equal(maskProfanity("[PESSOA] falou com [LOCAL]"), "[PESSOA] falou com [LOCAL]");
  });
});

describe("production statistics", () => {
  it("uses the nearest-rank percentile", () => {
    const values = Array.from({ length: 20 }, (_, i) => 20 - i);
    assert.equal(percentile(values, 95), 19);
    assert.equal(percentile(values, 50), 10);
    assert.equal(percentile([], 50), null);
  });

  it("answers with zeros and nulls before the first submission", () => {
    assert.deepEqual(productionStats(), {
      total_submissions: 0, distinct_submitters: 0, classified: 0, latency_p50_seconds: null,
      latency_p95_seconds: null, alerts_sent: 0, mean_submission_to_alert_seconds: null, first_public_day: null,
    });
  });
});
