import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_n8n_workflows as build  # noqa: E402
import check_n8n_export as check  # noqa: E402

FILES = sorted((ROOT / "n8n" / "workflows").glob("*.json"))


def test_repository_workflows_are_clean():
    assert FILES, "no workflow files"
    for path in FILES:
        assert check.check_file(path) == [], path.name


def test_files_match_the_generator():
    for name, builder in build.BUILDERS.items():
        on_disk = json.loads((ROOT / "n8n" / "workflows" / f"{name}.json").read_text(encoding="utf-8"))
        assert on_disk == builder(), f"{name}.json is stale: run python scripts/build_n8n_workflows.py"


def test_execution_data_is_never_saved():
    for path in FILES:
        settings = json.loads(path.read_text(encoding="utf-8"))["settings"]
        assert settings["saveDataSuccessExecution"] == "none"
        assert settings["saveDataErrorExecution"] == "none"


def test_external_channels_ship_disabled():
    deliver = build.deliver_to_channels()
    by_name = {n["name"]: n for n in deliver["nodes"]}
    assert by_name["Telegram"]["disabled"] and by_name["Email"]["disabled"]
    assert "credentials" not in by_name["Telegram"] and "credentials" not in by_name["Email"]
    assert build.intake_google_forms()["active"] is False


def test_checker_catches_secrets_and_credentials():
    wf = build.critical_alert()
    wf["nodes"][1]["credentials"] = {"telegramApi": {"id": "7", "name": "Bot", "data": "x"}}
    wf["nodes"][2]["parameters"]["jsCode"] += "// token 123456789:AAEhBP0av18Bd6qkLp3fZr8vjfBdA9xq3Xo"
    wf["pinData"] = {"Webhook": [{"json": {"comment": "texto"}}]}
    wf["shared"] = [{"project": {"name": "Fulano <fulano@gmail.com>"}}]
    problems = "\n".join(check.check_workflow(wf, "wf"))
    for expected in ("references credential", "has fields", "telegram bot token", "pinData",
                     "should not be exported", "email address"):
        assert expected in problems, expected
