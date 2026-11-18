// Number parsing of the hours-saved estimator (site/src/static/js). No npm packages:
// docker run --rm -v "$PWD":/w -w /w node:24-bookworm-slim node --test site/test/
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseInteger, parseNumber } from "../src/static/js/lib/format.js";
import { compute, readInput } from "../src/static/js/estimator.js";

test("parseInteger reads plain digits and correct grouping", () => {
  assert.equal(parseInteger("1000", "pt"), 1000);
  assert.equal(parseInteger("10000", "pt"), 10000);
  assert.equal(parseInteger("1.000", "pt"), 1000);
  assert.equal(parseInteger("10.000", "pt"), 10000);
  assert.equal(parseInteger("1.000.000", "pt"), 1000000);
  assert.equal(parseInteger(" 2 500 ", "pt"), 2500);
  assert.equal(parseInteger("2 500", "pt"), 2500);
  assert.equal(parseInteger("1,000", "en"), 1000);
  assert.equal(parseInteger("1000", "en"), 1000);
  assert.equal(parseInteger("0", "pt"), 0);
});

test("parseInteger never changes the magnitude: misplaced separators are invalid", () => {
  // The reported bug: "1.000" with one more zero was read as 1. It must not become 1 or 10000 silently.
  for (const text of ["1.0000", "1.00", "1.5000", "2.5", "0.5", "10.00", "0.500", "1..000", "1.000.", ".000", "1.000,"]) {
    assert.equal(parseInteger(text, "pt"), null, text);
  }
  for (const text of ["1,0000", "2,5", "0,5", "1.000", "1.5"]) {
    assert.equal(parseInteger(text, "en"), null, text);
  }
  for (const text of ["", "-", ".", "abc", "1e3", "2,5", "1,000"]) {
    assert.equal(parseInteger(text, "pt"), null, text);
  }
});

test("parseNumber reads decimals in both languages", () => {
  assert.equal(parseNumber("1,5", "pt"), 1.5);
  assert.equal(parseNumber("1.5", "pt"), 1.5);
  assert.equal(parseNumber("10.00", "pt"), 10);
  assert.equal(parseNumber("1.234,5", "pt"), 1234.5);
  assert.equal(parseNumber("1.000", "pt"), 1000);
  assert.equal(parseNumber("1,234.5", "en"), 1234.5);
  assert.equal(parseNumber("1,5", "en"), 1.5);
  assert.equal(parseNumber("45", "pt"), 45);
  assert.equal(parseNumber("−2,5", "pt"), -2.5);
});

test("parseNumber reads a group starting with 0 as a decimal and rejects broken text", () => {
  assert.equal(parseNumber("0.100", "pt"), 0.1);
  assert.equal(parseNumber("0.050", "pt"), 0.05);
  assert.equal(parseNumber("0,050", "en"), 0.05);
  for (const text of ["1.5.", "1.0.0", "1.2.3,5", "1,5,0", ",5", "1,", "", "abc"]) {
    assert.equal(parseNumber(text, "pt"), null, text);
  }
});

function field(value, kind, min, max) {
  return { value, dataset: { kind, min: String(min), max: String(max) } };
}

test("readInput: counts are whole numbers, other fields keep decimals", () => {
  assert.equal(readInput(field("10000", "count", 1, 1000000), "pt"), 10000);
  assert.equal(readInput(field("10.000", "count", 1, 1000000), "pt"), 10000);
  assert.equal(readInput(field("1.0000", "count", 1, 1000000), "pt"), null);
  assert.equal(readInput(field("2.5", "count", 1, 52), "pt"), null);
  assert.equal(readInput(field("2,5", "count", 1, 52), "pt"), null);
  assert.equal(readInput(field("0", "count", 1, 52), "pt"), null);
  assert.equal(readInput(field("45", "share", 0, 100), "pt"), 0.45);
  assert.equal(readInput(field("1,5", "minutes", 0, 120), "pt"), 1.5);
  assert.equal(readInput(field("0.100", "minutes", 0, 120), "pt"), 0.1);
});

test("compute matches the build for the default assumptions", () => {
  const values = {
    responses_per_cycle: 1000, comment_share: 0.45, cycles_per_year: 2,
    minutes_read_classify: 1.5, minutes_anonymize: 1.0, review_share: 0.15, minutes_per_review: 2.0,
  };
  const result = compute(values, 6.0);
  assert.equal(result.comments, 900);
  assert.equal(result.manual, 37.5);
  assert.ok(Math.abs(result.review - 4.5) < 1e-9);
  assert.ok(Math.abs(result.machine - 1.5) < 1e-9);
});
