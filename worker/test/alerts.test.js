import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import { alertPayload, messageHtml, sendWebhook } from "../alerts.js";
import { SETTINGS } from "../settings.js";
import {
  FakeAI, VALID, WEBHOOK, makeApp, makeCtx, makeEnv, mockFetch, postSubmission, seedSubmissions, stage1, standardFetch,
  verdict,
} from "./helpers.js";

let fetchMock;
afterEach(() => fetchMock?.restore());

// Expected strings computed by src/enps/alerts.py message_html for the same views.
const PYTHON_CRITICAL =
  "<b>Caso crítico na pesquisa de clima</b>\nTipos: Assédio moral, Burnout e saúde mental\n" +
  "Temas: Liderança, Saúde e bem-estar\nÁrea: Operações\nRecebido em 10/11/2026\n" +
  "<i>“Meu gestor [PESSOA] grita &amp; diz &lt;coisas&gt; \"feias\"”</i>\n" +
  '<a href="https://voz-sem-nome.andreiludescher.com/painel/#criticos">Ver os casos críticos no painel</a>';
const PYTHON_HIDDEN_AREA =
  "<b>Caso crítico na pesquisa de clima</b>\nTipo: Assédio sexual\nÁrea omitida (grupo pequeno)\n" +
  "Recebido em 11/11/2026\n" +
  '<a href="https://voz-sem-nome.andreiludescher.com/painel/#criticos">Ver os casos críticos no painel</a>';

const CRITICAL_COMMENT = "Meu gestor grita comigo na frente de todos e me humilha toda semana.";
const criticalAI = (count = 1) => new FakeAI({
  classify: Array(count).fill(stage1({ t: ["lid"], s: "neg", c: true, k: ["am"], e: "grita comigo" })),
  verify: Array(count).fill(verdict({ c: true, k: ["am"], e: "me humilha" })),
});

/** Submit `comment` through `app` and wait for its classification. */
async function submitAndSettle(app, env, comment, ip = "203.0.113.7") {
  const ctx = makeCtx();
  const response = await app.fetch(postSubmission({ ...VALID, comment }, { "CF-Connecting-IP": ip }), env, ctx);
  assert.equal(response.status, 202);
  return ctx.settle();
}

describe("message_html (port of enps.alerts)", () => {
  it("matches the Python message, with HTML escaped and the date in Sao Paulo", () => {
    const view = {
      types: ["am", "bo"], themes: ["lid", "bem"], area: "operacoes",
      excerpt: 'Meu gestor [PESSOA] grita & diz <coisas> "feias"', submitted_at: "2026-11-11T02:30:00.000+00:00",
    };
    assert.equal(messageHtml(view, SETTINGS), PYTHON_CRITICAL);
  });

  it("hides the area and leaves out empty themes and excerpt", () => {
    const view = { types: ["as"], themes: [], area: null, excerpt: "", submitted_at: "2026-11-11T15:00:00.000+00:00" };
    assert.equal(messageHtml(view, SETTINGS), PYTHON_HIDDEN_AREA);
  });

  it("builds the n8n payload of the local worker", () => {
    const view = {
      id: 8, mode: "digest", types: ["as"], themes: [], area: null, excerpt: "",
      submitted_at: "2026-11-11T15:00:00.000+00:00", detected_at: "2026-11-11T15:00:01.000+00:00",
    };
    assert.deepEqual(alertPayload(view, SETTINGS), {
      alert_id: 8, dataset: "public", mode: "digest", types: ["as"], themes: [], area: null, excerpt: "",
      submitted_at: "2026-11-11T15:00:00.000+00:00", detected_at: "2026-11-11T15:00:01.000+00:00",
      message_html: PYTHON_HIDDEN_AREA, channels: [],
    });
  });
});

describe("alerts of critical results", () => {
  it("records an alert with submitted_at, detected_at and delivered_at, without a webhook", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: criticalAI() });
    const { app, now } = makeApp();
    const ctx = makeCtx();
    await app.fetch(postSubmission({ ...VALID, comment: CRITICAL_COMMENT }), env, ctx);
    now.advance(3000);
    assert.deepEqual(await ctx.settle(), ["done"]);
    const row = env.DB.row("SELECT * FROM submissions");
    assert.equal(row.critical, 1);
    assert.equal(row.critical_types, '["am"]');
    const alert = env.DB.row("SELECT * FROM alerts");
    assert.deepEqual(alert, {
      id: 1, public_id: row.public_id, critical_types: '["am"]', submitted_at: "2026-11-11T15:00:00.000+00:00",
      detected_at: "2026-11-11T15:00:03.000+00:00", delivered_at: "2026-11-11T15:00:03.000+00:00", mode: "immediate",
    });
    assert.equal(env.DB.row("SELECT value FROM daily WHERE key = 'critical'").value, 1);
    assert.equal(fetchMock.calls.filter((c) => c.url.href === WEBHOOK).length, 0);
  });

  it("switches to digest after alerts.immediate_per_hour immediate alerts in an hour", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: criticalAI(6), RL: undefined, ALERT_WEBHOOK_URL: WEBHOOK, ALERT_WEBHOOK_TOKEN: "hook-token" });
    const { app, now } = makeApp();
    for (let i = 0; i < 5; i += 1) {
      await submitAndSettle(app, env, `${CRITICAL_COMMENT} Caso ${i}.`, `198.51.100.${i}`);
      now.advance(60_000);
    }
    assert.deepEqual(env.DB.rows("SELECT mode FROM alerts ORDER BY id").map((r) => r.mode),
      ["immediate", "immediate", "immediate", "digest", "digest"]);
    assert.equal(fetchMock.calls.filter((c) => c.url.href === WEBHOOK).length, 3);
    now.advance(3600_000);
    await submitAndSettle(app, env, `${CRITICAL_COMMENT} Caso tardio.`, "198.51.100.99");
    assert.equal(env.DB.row("SELECT mode FROM alerts ORDER BY id DESC LIMIT 1").mode, "immediate");
  });

  it("posts the n8n JSON with X-Internal-Token, showing the area only when it is visible", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: criticalAI(2), RL: undefined, ALERT_WEBHOOK_URL: WEBHOOK, ALERT_WEBHOOK_TOKEN: "hook-token" });
    const { app } = makeApp();
    await submitAndSettle(app, env, CRITICAL_COMMENT);
    let [call] = fetchMock.calls.filter((c) => c.url.href === WEBHOOK);
    assert.equal(call.method, "POST");
    assert.equal(call.headers.get("X-Internal-Token"), "hook-token");
    assert.match(call.headers.get("Content-Type"), /^application\/json/);
    let payload = JSON.parse(call.body);
    assert.deepEqual(Object.keys(payload), [
      "alert_id", "dataset", "mode", "types", "themes", "area", "excerpt", "submitted_at", "detected_at",
      "message_html", "channels",
    ]);
    assert.equal(payload.alert_id, 1);
    assert.equal(payload.dataset, "public");
    assert.deepEqual(payload.types, ["am"]);
    assert.deepEqual(payload.themes, ["lid"]);
    assert.equal(payload.area, null); // one answer in the area: below the minimum group size
    assert.equal(payload.excerpt, CRITICAL_COMMENT);
    assert.ok(payload.message_html.includes("Área omitida (grupo pequeno)"));
    assert.deepEqual(payload.channels, []);

    await seedSubmissions(env.DB, Array.from({ length: 5 }, () => ({ area: "operacoes" })));
    await submitAndSettle(app, env, `${CRITICAL_COMMENT} De novo.`, "198.51.100.77");
    call = fetchMock.calls.filter((c) => c.url.href === WEBHOOK)[1];
    payload = JSON.parse(call.body);
    assert.equal(payload.area, "operacoes");
    assert.ok(payload.message_html.includes("Área: Operações"));
  });

  it("counts the area like the public dashboard, without the answers before the start day", async () => {
    // Five setup tests in the same area on the day before the start must not make it visible.
    fetchMock = standardFetch();
    const env = makeEnv({
      AI: criticalAI(2), RL: undefined, ALERT_WEBHOOK_URL: WEBHOOK, ALERT_WEBHOOK_TOKEN: "hook-token",
      PUBLIC_START_DAY: "2026-11-11",
    });
    const { app } = makeApp();
    await seedSubmissions(env.DB, Array.from({ length: 5 }, () => ({ area: "operacoes", created_at: "2026-11-10T20:00:00.000+00:00" })));
    await submitAndSettle(app, env, CRITICAL_COMMENT);
    let payload = JSON.parse(fetchMock.calls.filter((c) => c.url.href === WEBHOOK)[0].body);
    assert.equal(payload.area, null); // 6 answers in D1, 1 since the start
    assert.ok(payload.message_html.includes("Área omitida (grupo pequeno)"));

    await seedSubmissions(env.DB, Array.from({ length: 4 }, () => ({ area: "operacoes", created_at: "2026-11-11T12:00:00.000+00:00" })));
    await submitAndSettle(app, env, `${CRITICAL_COMMENT} De novo.`, "198.51.100.77");
    payload = JSON.parse(fetchMock.calls.filter((c) => c.url.href === WEBHOOK)[1].body);
    assert.equal(payload.area, "operacoes"); // 6 since the start
  });

  it("never fails the classification when the webhook fails", async () => {
    for (const handler of [() => new Response("down", { status: 500 }), () => Promise.reject(new TypeError("network"))]) {
      fetchMock = mockFetch((call) => (call.url.href === WEBHOOK ? handler() : new Response(JSON.stringify({ success: true }))));
      const env = makeEnv({ AI: criticalAI(), ALERT_WEBHOOK_URL: WEBHOOK });
      const { app } = makeApp();
      assert.deepEqual(await submitAndSettle(app, env, CRITICAL_COMMENT), ["done"]);
      assert.equal(env.DB.rows("SELECT id FROM alerts").length, 1);
      fetchMock.restore();
    }
  });

  it("sendWebhook reports success and failure without throwing", async () => {
    fetchMock = mockFetch(() => new Response(null, { status: 204 }));
    assert.equal(await sendWebhook({ ALERT_WEBHOOK_URL: WEBHOOK }, { alert_id: 1 }), true);
    assert.equal(fetchMock.calls[0].headers.get("X-Internal-Token"), "");
    fetchMock.restore();
    fetchMock = mockFetch(() => new Response("", { status: 401 }));
    assert.equal(await sendWebhook({ ALERT_WEBHOOK_URL: WEBHOOK }, { alert_id: 1 }), false);
  });
});
