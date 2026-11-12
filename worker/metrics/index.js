// Public dashboard and production statistics of the Worker: a port of src/enps/metrics with the
// same JSON output, proven by tests/fixtures/dashboard_cases.json (written by
// scripts/build_dashboard_fixtures.py with the Python code). No dependencies and no I/O.

export { buildDashboard, pipelineInfo, visibleAreaCodes } from "./aggregates.js";
export { DASHBOARD_CONFIG } from "./data.js";
export { category, enpsValue, summarize } from "./enps.js";
export { maskProfanity, publicExcerpt, truncate } from "./excerpt.js";
export { percentile, productionStats } from "./production.js";
export { partitionGroups, suppressCells } from "./suppression.js";
