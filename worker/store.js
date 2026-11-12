// D1 storage of the live demo. Every statement is parameterized; no raw comment and no IP
// address is ever written (only the anonymized comment and a daily-salted HMAC of the IP).
//
// The schema is created at runtime with CREATE TABLE IF NOT EXISTS, once per isolate and
// database binding (ensureSchema). Tables:
//   submissions       one row per answer; classification columns filled when it finishes
//   alerts            critical cases: ids, types and timestamps only
//   daily             counters per Sao Paulo day (submissions, rejections by reason, ...)
//   submitters_today  HMAC(ip, salt of the day) with the submissions of that IP today
//   meta              key/value: daily IP salts, the day the Workers AI quota ran out

export const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    tracking_hash TEXT UNIQUE,
    created_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    area TEXT NOT NULL,
    tenure TEXT NOT NULL,
    lang TEXT NOT NULL,
    comment_anon TEXT NOT NULL DEFAULT '',
    comment_chars INTEGER NOT NULL DEFAULT 0,
    pii_counts TEXT NOT NULL DEFAULT '{}',
    anonymizer_version TEXT,
    dedupe_key TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    themes TEXT,
    sentiment TEXT,
    critical INTEGER NOT NULL DEFAULT 0,
    critical_types TEXT NOT NULL DEFAULT '[]',
    critical_sources TEXT NOT NULL DEFAULT '[]',
    evidence TEXT,
    model TEXT,
    prompt_version TEXT,
    verifier_version TEXT,
    stages TEXT,
    ai_ms INTEGER,
    prompt_tokens INTEGER,
    output_tokens INTEGER
  )`,
  // tracking_hash and public_id are indexed by their UNIQUE constraints.
  "CREATE INDEX IF NOT EXISTS submissions_created_at ON submissions (created_at)",
  "CREATE INDEX IF NOT EXISTS submissions_status ON submissions (status)",
  "CREATE INDEX IF NOT EXISTS submissions_dedupe_key ON submissions (dedupe_key)",
  `CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL,
    critical_types TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    delivered_at TEXT,
    mode TEXT NOT NULL
  )`,
  "CREATE INDEX IF NOT EXISTS alerts_detected_at ON alerts (detected_at)",
  `CREATE TABLE IF NOT EXISTS daily (
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
  )`,
  `CREATE TABLE IF NOT EXISTS submitters_today (
    day TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    submissions INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, ip_hash)
  )`,
  "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
];

// Columns the dashboard reads (worker/metrics/aggregates.js), in id order.
const DASHBOARD_COLUMNS =
  "public_id, created_at, score, area, tenure, comment_anon, status, themes, sentiment, critical, " +
  "critical_types, finished_at, model, prompt_version, anonymizer_version";

const INSERT_SUBMISSION = `
  INSERT INTO submissions (
    public_id, tracking_hash, created_at, score, area, tenure, lang, comment_anon, comment_chars,
    pii_counts, anonymizer_version, dedupe_key, status, attempts, started_at, finished_at
  )
  SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16
  WHERE ?12 IS NULL
     OR NOT EXISTS (SELECT 1 FROM submissions WHERE dedupe_key = ?12 AND created_at >= ?17)
  RETURNING id`;

const FINISH_SUBMISSION = `
  UPDATE submissions
     SET status = ?, finished_at = ?, themes = ?, sentiment = ?, critical = ?, critical_types = ?,
         critical_sources = ?, evidence = ?, model = ?, prompt_version = ?, verifier_version = ?,
         stages = ?, ai_ms = ?, prompt_tokens = ?, output_tokens = ?
   WHERE id = ? AND status = 'processing' AND attempts = ?`;

const schemaReady = new WeakMap();

/** Create the tables once per isolate and binding; a failed attempt is retried next time. */
export function ensureSchema(db) {
  let ready = schemaReady.get(db);
  if (!ready) {
    ready = db.batch(SCHEMA.map((sql) => db.prepare(sql))).then(() => undefined);
    ready.catch(() => schemaReady.delete(db));
    schemaReady.set(db, ready);
  }
  return ready;
}

/** Thin layer over the D1 binding: one method per statement the Worker runs. */
export class Store {
  constructor(db) {
    this.db = db;
  }

  ready() {
    return ensureSchema(this.db);
  }

  ping() {
    return this.db.prepare("SELECT 1 AS ok").first("ok");
  }

  // ---------------------------------------------------------------- meta and counters

  async meta(key) {
    const row = await this.db.prepare("SELECT value FROM meta WHERE key = ?").bind(key).first();
    return row ? row.value : null;
  }

  setMeta(key, value) {
    return this.db
      .prepare("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value")
      .bind(key, value)
      .run();
  }

  /** The value stored under `key`, storing `candidate` first when there is none (race safe). */
  async metaOrInsert(key, candidate) {
    const [, read] = await this.db.batch([
      this.db.prepare("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)").bind(key, candidate),
      this.db.prepare("SELECT value FROM meta WHERE key = ?").bind(key),
    ]);
    return read.results[0].value;
  }

  bumpStatement(day, key, amount = 1) {
    return this.db
      .prepare(
        "INSERT INTO daily (day, key, value) VALUES (?, ?, ?) " +
          "ON CONFLICT (day, key) DO UPDATE SET value = value + excluded.value",
      )
      .bind(day, key, amount);
  }

  /** Add to counters of one day: bump(day, {submissions: 1, critical: 0}) skips zeros. */
  async bump(day, counts) {
    const statements = Object.entries(counts)
      .filter(([, amount]) => amount)
      .map(([key, amount]) => this.bumpStatement(day, key, amount));
    if (statements.length) await this.db.batch(statements);
  }

  dailyRows() {
    return this.all("SELECT day, key, value FROM daily ORDER BY day, key");
  }

  async dailyValues(day) {
    const rows = await this.all("SELECT key, value FROM daily WHERE day = ?", day);
    return Object.fromEntries(rows.map((r) => [r.key, r.value]));
  }

  // ---------------------------------------------------------------- limits

  /**
   * Everything the limits need in one round trip: submissions of this IP today, the
   * `globalMax`-th newest submission after `hourCutoff` (present when the hour is full) and
   * the day the Workers AI quota ran out.
   */
  async limitState({ day, ipHash, hourCutoff, globalMax, quotaKey }) {
    const [ip, nth, quota] = await this.db.batch([
      this.db.prepare("SELECT submissions FROM submitters_today WHERE day = ? AND ip_hash = ?").bind(day, ipHash),
      this.db
        .prepare("SELECT created_at FROM submissions WHERE created_at > ? ORDER BY created_at DESC LIMIT 1 OFFSET ?")
        .bind(hourCutoff, Math.max(0, globalMax - 1)),
      this.db.prepare("SELECT value FROM meta WHERE key = ?").bind(quotaKey),
    ]);
    return {
      ipSubmissions: ip.results[0]?.submissions ?? 0,
      hourFullSince: nth.results[0]?.created_at ?? null,
      quotaDay: quota.results[0]?.value ?? null,
    };
  }

  recordSubmitter(day, ipHash) {
    return this.db
      .prepare(
        "INSERT INTO submitters_today (day, ip_hash, submissions) VALUES (?, ?, 1) " +
          "ON CONFLICT (day, ip_hash) DO UPDATE SET submissions = submissions + 1",
      )
      .bind(day, ipHash)
      .run();
  }

  /** True the first time an IP gets a submission accepted on `day`. */
  async firstAccepted(day, ipHash) {
    const result = await this.db
      .prepare("UPDATE submitters_today SET accepted = 1 WHERE day = ? AND ip_hash = ? AND accepted = 0")
      .bind(day, ipHash)
      .run();
    return result.meta.changes === 1;
  }

  // ---------------------------------------------------------------- submissions

  /**
   * Insert unless the same dedupe_key arrived since `dedupeSince`. Returns the new id, or null
   * for a duplicate. Throws on a UNIQUE conflict of the generated ids (the caller retries).
   */
  async insertSubmission(row, dedupeSince) {
    const inserted = await this.db
      .prepare(INSERT_SUBMISSION)
      .bind(
        row.public_id, row.tracking_hash, row.created_at, row.score, row.area, row.tenure, row.lang,
        row.comment_anon, row.comment_chars, row.pii_counts, row.anonymizer_version, row.dedupe_key,
        row.status, row.attempts, row.started_at, row.finished_at, dedupeSince,
      )
      .first();
    return inserted ? inserted.id : null;
  }

  submissionById(id) {
    return this.db.prepare("SELECT * FROM submissions WHERE id = ?").bind(id).first();
  }

  submissionByTracking(hash) {
    return this.db.prepare("SELECT * FROM submissions WHERE tracking_hash = ?").bind(hash).first();
  }

  /** Waiting rows of `status` with a smaller id: the queue position minus one. */
  async waitingAhead(status, id) {
    return (await this.db
      .prepare("SELECT count(*) AS n FROM submissions WHERE status = ? AND id < ?")
      .bind(status, id)
      .first("n")) ?? 0;
  }

  /** Mean seconds from start to finish of the last `window` finished jobs; null without any. */
  async averageJobSeconds(window) {
    const value = await this.db
      .prepare(
        `SELECT avg((julianday(finished_at) - julianday(started_at)) * 86400.0) AS s FROM (
           SELECT started_at, finished_at FROM submissions
            WHERE status IN ('done', 'review') AND started_at IS NOT NULL AND finished_at IS NOT NULL
            ORDER BY id DESC LIMIT ?)`,
      )
      .bind(window)
      .first("s");
    return value ?? null;
  }

  /** Move a row to a new attempt; false when another run claimed it first. */
  async claim(id, fromStatus, attempts, startedAt) {
    const result = await this.db
      .prepare(
        "UPDATE submissions SET status = 'processing', attempts = attempts + 1, started_at = ? " +
          "WHERE id = ? AND status = ? AND attempts = ?",
      )
      .bind(startedAt, id, fromStatus, attempts)
      .run();
    return result.meta.changes === 1;
  }

  /** Store a finished classification of attempt `attempts`; false when the row moved on. */
  async finish(id, attempts, fields) {
    const result = await this.db
      .prepare(FINISH_SUBMISSION)
      .bind(
        fields.status, fields.finished_at, fields.themes, fields.sentiment, fields.critical,
        fields.critical_types, fields.critical_sources, fields.evidence, fields.model,
        fields.prompt_version, fields.verifier_version, fields.stages, fields.ai_ms,
        fields.prompt_tokens, fields.output_tokens, id, attempts,
      )
      .run();
    return result.meta.changes === 1;
  }

  /** Quota hit: wait for the reset; the attempt is given back. */
  async parkForQuota(id, attempts) {
    const result = await this.db
      .prepare(
        "UPDATE submissions SET status = 'queued_quota', started_at = NULL, attempts = attempts - 1 " +
          "WHERE id = ? AND status = 'processing' AND attempts = ?",
      )
      .bind(id, attempts)
      .run();
    return result.meta.changes === 1;
  }

  async fail(id, finishedAt, { status = "processing", attempts = null } = {}) {
    const result = await this.db
      .prepare(
        "UPDATE submissions SET status = 'failed', finished_at = ? WHERE id = ? AND status = ? " +
          "AND (? IS NULL OR attempts = ?)",
      )
      .bind(finishedAt, id, status, attempts, attempts)
      .run();
    return result.meta.changes === 1;
  }

  /** Rows the cron retries: stuck in processing since before `stuckBefore`, or waiting for quota. */
  retryCandidates({ stuckBefore, includeQuota, limit }) {
    return this.all(
      `SELECT id, public_id, created_at, area, comment_anon, status, attempts, started_at
         FROM submissions
        WHERE (status = 'processing' AND started_at < ?) OR (? = 1 AND status = 'queued_quota')
        ORDER BY id LIMIT ?`,
      stuckBefore, includeQuota ? 1 : 0, limit,
    );
  }

  // ---------------------------------------------------------------- reads for the site

  dashboardRows() {
    return this.all(`SELECT ${DASHBOARD_COLUMNS} FROM submissions ORDER BY id`);
  }

  areaCounts() {
    return this.all("SELECT area, count(*) AS n FROM submissions GROUP BY area");
  }

  async statusCounts() {
    const rows = await this.all(
      "SELECT status, count(*) AS n FROM submissions WHERE status IN ('processing', 'queued_quota') GROUP BY status",
    );
    return Object.fromEntries(rows.map((r) => [r.status, r.n]));
  }

  /** created_at and finished_at of finished jobs, newest first, created after `since` when given. */
  latencies({ since = null, limit }) {
    return this.all(
      `SELECT created_at, finished_at FROM submissions
        WHERE status IN ('done', 'review') AND finished_at IS NOT NULL AND (? IS NULL OR created_at >= ?)
        ORDER BY id DESC LIMIT ?`,
      since, since, limit,
    );
  }

  alertTimes() {
    return this.all("SELECT submitted_at, delivered_at FROM alerts ORDER BY id");
  }

  async alertsSince(since, mode = null) {
    return (await this.db
      .prepare("SELECT count(*) AS n FROM alerts WHERE detected_at >= ? AND (? IS NULL OR mode = ?)")
      .bind(since, mode, mode)
      .first("n")) ?? 0;
  }

  async insertAlert(alert) {
    const row = await this.db
      .prepare(
        "INSERT INTO alerts (public_id, critical_types, submitted_at, detected_at, delivered_at, mode) " +
          "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
      )
      .bind(alert.public_id, alert.critical_types, alert.submitted_at, alert.detected_at, alert.delivered_at, alert.mode)
      .first();
    return row.id;
  }

  // ---------------------------------------------------------------- retention

  /** Delete expired rows; returns how many went per table. */
  async purge({ submissionsBefore, alertsBefore, today }) {
    const results = await this.db.batch([
      this.db.prepare("DELETE FROM submissions WHERE created_at < ?").bind(submissionsBefore),
      this.db.prepare("DELETE FROM alerts WHERE detected_at < ?").bind(alertsBefore),
      this.db.prepare("DELETE FROM submitters_today WHERE day < ?").bind(today),
      this.db.prepare("DELETE FROM meta WHERE key >= 'ip_salt:' AND key < ?").bind(`ip_salt:${today}`),
    ]);
    const [submissions, alerts, submitters, salts] = results.map((r) => r.meta.changes);
    return { submissions, alerts, submitters, salts };
  }

  // ---------------------------------------------------------------- helpers

  async all(sql, ...values) {
    const statement = values.length ? this.db.prepare(sql).bind(...values) : this.db.prepare(sql);
    return (await statement.all()).results;
  }
}
