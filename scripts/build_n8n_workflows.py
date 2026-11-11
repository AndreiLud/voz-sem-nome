"""Write the n8n workflows of the project to n8n/workflows/*.json.

The workflows are generated, not hand-edited, so they stay consistent and reviewable:

    python scripts/build_n8n_workflows.py

Active in the default install (everything stays on the server):
  critical-alert       webhook from the queue worker -> record the delivery -> deliver to channels
  alert-digest         every 5 min: stale alerts and the grouped digest -> record -> deliver to channels
  maintenance-daily    03:17 every day: purge old public submissions -> daily summary -> deliver to channels
  deliver-to-channels  sub-workflow: routes each message to Telegram or email. Both nodes ship disabled and
                       without credentials, and the API sends an empty channel list by default
Imported but inactive (needs a credential you add in n8n):
  intake-google-forms  reads new Google Forms answers from the linked sheet and sends them to the API

To turn on a channel: create its credential in n8n, select it in the node of "Deliver to channels", enable the
node, fill the placeholders (chat id or addresses), publish, and add the channel to alerts.channels in
config/settings.yaml (or ENPS_ALERTS_CHANNELS='["telegram"]').

No credential secret is ever written here. The only credential reference is "Internal API", by name and a
placeholder id; the value lives encrypted in n8n.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "n8n" / "workflows"
API = "http://api-internal:8002/v1/internal"
INTERNAL_CRED = {"httpHeaderAuth": {"id": "internalApiCredential", "name": "Internal API"}}
SETTINGS = {
    "executionOrder": "v1",
    "timezone": "America/Sao_Paulo",
    # Answers from Google Forms arrive raw: never keep execution data.
    "saveDataSuccessExecution": "none",
    "saveDataErrorExecution": "none",
    "saveManualExecutions": False,
    "callerPolicy": "workflowsFromSameOwner",
}
IDS = {
    "critical-alert": "VsnCriticalAlert",
    "alert-digest": "VsnAlertDigest01",
    "maintenance-daily": "VsnMaintenance01",
    "deliver-to-channels": "VsnDeliverChan01",
    "intake-google-forms": "VsnIntakeForms01",
}

FAN_OUT_JS = """
// One item per external channel listed by the API (alerts.channels in config/settings.yaml).
// With no channel configured nothing goes out: the alert is already recorded on the server.
const out = [];
for (const item of MESSAGES) {
  const message = item.json.message_html;
  for (const channel of item.json.channels || []) {
    out.push({ json: { channel, message_html: message } });
  }
}
return out;
"""


def _id(workflow: str, node: str) -> str:
    """Stable node ids, so regenerating gives the same file."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"voz-sem-nome/{workflow}/{node}"))


def node(workflow: str, name: str, type_: str, version: float, position: tuple[int, int],
         parameters: dict, **extra) -> dict:
    data = {"parameters": parameters, "id": _id(workflow, name), "name": name,
            "type": type_, "typeVersion": version, "position": list(position)}
    data.update(extra)
    return data


def http(workflow: str, name: str, method: str, url: str, position: tuple[int, int],
         body: str | None = None) -> dict:
    params: dict = {"method": method, "url": url, "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth", "options": {"timeout": 15000}}
    if body is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    return node(workflow, name, "n8n-nodes-base.httpRequest", 4.2, position, params,
                credentials=INTERNAL_CRED, retryOnFail=True, maxTries=3, waitBetweenTries=2000)


def code(workflow: str, name: str, js: str, position: tuple[int, int]) -> dict:
    return node(workflow, name, "n8n-nodes-base.code", 2, position, {"jsCode": js.strip() + "\n"})


def fan_out(workflow: str, source: str, position: tuple[int, int]) -> dict:
    return code(workflow, "Fan out to channels", FAN_OUT_JS.replace("MESSAGES", source), position)


def deliver(workflow: str, position: tuple[int, int]) -> dict:
    return node(workflow, "Deliver to channels", "n8n-nodes-base.executeWorkflow", 1.2, position, {
        "workflowId": {"__rl": True, "value": IDS["deliver-to-channels"], "mode": "id"},
        "workflowInputs": {"mappingMode": "defineBelow", "value": {}, "matchingColumns": [],
                           "schema": [], "attemptToConvertTypes": False, "convertFieldsToString": True},
        "mode": "each",
        "options": {"waitForSubWorkflow": True},
    })


def link(*pairs: tuple) -> dict:
    connections: dict = {}
    for pair in pairs:
        source, target, output = (*pair, 0) if len(pair) == 2 else pair
        outputs = connections.setdefault(source, {"main": []})["main"]
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": 0})
    return connections


def workflow(name: str, title: str, nodes: list[dict], connections: dict, *, active: bool,
             description: str) -> dict:
    return {
        "id": IDS[name], "name": title, "active": active, "nodes": nodes,
        "connections": connections, "settings": dict(SETTINGS), "pinData": {},
        "meta": {"templateCredsSetupCompleted": True}, "tags": [], "description": description,
    }


def critical_alert() -> dict:
    w = "critical-alert"
    nodes = [
        node(w, "Critical case from worker", "n8n-nodes-base.webhook", 2.1, (0, 0),
             {"httpMethod": "POST", "path": "critical-alert", "authentication": "headerAuth",
              "responseMode": "onReceived", "options": {}},
             credentials=INTERNAL_CRED, webhookId=_id(w, "webhook")),
        http(w, "Record delivery", "POST", f"={API}/alerts/{{{{ $json.body.alert_id }}}}/sent", (240, 0)),
        fan_out(w, "[{ json: $('Critical case from worker').first().json.body }]", (480, 0)),
        deliver(w, (720, 0)),
    ]
    conns = link(("Critical case from worker", "Record delivery"), ("Record delivery", "Fan out to channels"),
                 ("Fan out to channels", "Deliver to channels"))
    return workflow(w, "Critical alert", nodes, conns, active=True,
                    description="The queue worker posts each critical case here. The alert is recorded as "
                                "delivered on the server and sent to the optional channels, if any.")


def alert_digest() -> dict:
    w = "alert-digest"
    collect_js = """
// Alerts the worker could not push, plus the grouped digest when it is due.
const data = $input.first().json;
const out = [];
for (const alert of data.immediate || []) {
  out.push({ json: { ids: [alert.alert_id], message_html: alert.message_html, channels: data.channels } });
}
if (data.digest) {
  out.push({ json: { ids: data.digest.ids, message_html: data.digest.message_html, channels: data.channels } });
}
return out;
"""
    nodes = [
        node(w, "Every 5 minutes", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 0),
             {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}}),
        http(w, "Pending alerts", "GET", f"{API}/alerts/pending", (240, 0)),
        code(w, "Collect messages", collect_js, (480, 0)),
        http(w, "Record delivery", "POST", f"{API}/alerts/sent", (720, 0),
             body="={{ JSON.stringify({ ids: $json.ids }) }}"),
        fan_out(w, "$('Collect messages').all()", (960, 0)),
        deliver(w, (1200, 0)),
    ]
    conns = link(("Every 5 minutes", "Pending alerts"), ("Pending alerts", "Collect messages"),
                 ("Collect messages", "Record delivery"), ("Record delivery", "Fan out to channels"),
                 ("Fan out to channels", "Deliver to channels"))
    return workflow(w, "Alert digest", nodes, conns, active=True,
                    description="Above the hourly limit, alerts wait and leave together in one digest. "
                                "This also delivers any alert the worker could not push.")


def maintenance() -> dict:
    w = "maintenance-daily"
    nodes = [
        node(w, "Every day at 03:17", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 0),
             {"rule": {"interval": [{"field": "cronExpression", "expression": "17 3 * * *"}]}}),
        http(w, "Purge old public submissions", "POST", f"{API}/maintenance/purge", (240, 0)),
        http(w, "Daily summary", "GET", f"{API}/summary/daily", (480, 0)),
        fan_out(w, "$input.all()", (720, 0)),
        deliver(w, (960, 0)),
    ]
    conns = link(("Every day at 03:17", "Purge old public submissions"),
                 ("Purge old public submissions", "Daily summary"), ("Daily summary", "Fan out to channels"),
                 ("Fan out to channels", "Deliver to channels"))
    return workflow(w, "Daily maintenance", nodes, conns, active=True,
                    description="Deletes public submissions older than the retention period and builds the "
                                "daily summary.")


def deliver_to_channels() -> dict:
    w = "deliver-to-channels"

    def rule(value: str) -> dict:
        return {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{"id": _id(w, f"rule-{value}"), "leftValue": "={{ $json.channel }}",
                                "rightValue": value, "operator": {"type": "string", "operation": "equals"}}],
                "combinator": "and",
            },
            "renameOutput": True,
            "outputKey": value,
        }
    nodes = [
        node(w, "Message to deliver", "n8n-nodes-base.executeWorkflowTrigger", 1.1, (0, 0),
             {"inputSource": "passthrough"}),
        node(w, "Channel", "n8n-nodes-base.switch", 3.2, (240, 0),
             {"rules": {"values": [rule("telegram"), rule("email")]}, "options": {}}),
        # Disabled and without a credential: n8n refuses to publish a workflow that points to a credential
        # that does not exist. Select your credential, enable the node and fill the placeholder to use it.
        node(w, "Telegram", "n8n-nodes-base.telegram", 1.2, (480, -100),
             {"resource": "message", "operation": "sendMessage", "chatId": "SET_YOUR_TELEGRAM_CHAT_ID",
              "text": "={{ $json.message_html }}",
              "additionalFields": {"appendAttribution": False, "parse_mode": "HTML",
                                   "disable_web_page_preview": True}},
             disabled=True),
        node(w, "Email", "n8n-nodes-base.emailSend", 2.1, (480, 100),
             {"fromEmail": "SET_SENDER_ADDRESS", "toEmail": "SET_YOUR_ADDRESS",
              "subject": "Voz sem nome: alerta da pesquisa de clima", "emailFormat": "html",
              "html": "={{ $json.message_html.replace(/\\n/g, '<br>') }}",
              "options": {"appendAttribution": False}},
             disabled=True),
    ]
    conns = link(("Message to deliver", "Channel"), ("Channel", "Telegram", 0), ("Channel", "Email", 1))
    return workflow(w, "Deliver to channels", nodes, conns, active=True,
                    description="Optional external channels. Off by default: both nodes are disabled and the API "
                                "sends an empty channel list.")


def intake_google_forms() -> dict:
    w = "intake-google-forms"
    map_js = """
// Google Forms writes one row per answer: the timestamp, then the questions in form order.
// Columns are read by position, so renaming a question does not break the flow.
return $input.all().map((item) => {
  const values = Object.values(item.json);
  return { json: { submitted_at: values[0], score: values[1], area: values[2], tenure: values[3],
                   comment: values[4] || '' } };
});
"""
    nodes = [
        node(w, "New form answer", "n8n-nodes-base.googleSheetsTrigger", 1, (0, 0),
             {"authentication": "serviceAccount",
              "documentId": {"__rl": True, "value": "SET_RESPONSES_SPREADSHEET_ID", "mode": "id"},
              "sheetName": {"__rl": True, "value": "gid=0", "mode": "list"},
              "event": "rowAdded", "pollTimes": {"item": [{"mode": "everyMinute"}]}, "options": {}}),
        code(w, "Read columns by position", map_js, (240, 0)),
        http(w, "Send to the API", "POST", f"{API}/responses", (480, 0), body="={{ JSON.stringify($json) }}"),
    ]
    return workflow(w, "Intake: Google Forms (optional)", nodes,
                    link(("New form answer", "Read columns by position"),
                         ("Read columns by position", "Send to the API")), active=False,
                    description="Optional. Link the form to a sheet, share it with a Google service account, add "
                                "that credential to the trigger and set the spreadsheet id. The API anonymizes "
                                "on arrival.")


BUILDERS = {
    "critical-alert": critical_alert, "alert-digest": alert_digest, "maintenance-daily": maintenance,
    "deliver-to-channels": deliver_to_channels, "intake-google-forms": intake_google_forms,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        if old.stem not in BUILDERS:
            old.unlink()
    for name, build in BUILDERS.items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote", path.relative_to(OUT.parents[1]))


if __name__ == "__main__":
    main()
