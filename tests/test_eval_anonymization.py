import csv
import json

from enps.anonymize import AnonymizationResult, PiiSpan
from enps.anonymize.pipeline import replace_spans
from enps.evaluation import anonymization as ev


def gold_span(text: str, value: str, pii_type: str) -> dict:
    start = text.index(value)
    return {"start": start, "end": start + len(value), "type": pii_type, "text": value}


def result(text: str, spans: list[PiiSpan]) -> AnonymizationResult:
    return AnonymizationResult(text=replace_spans(text, spans), spans=tuple(spans))


def span(text: str, value: str, pii_type: str) -> PiiSpan:
    start = text.index(value)
    return PiiSpan(start, start + len(value), pii_type, "regex")


T1 = "O Paulo Mendes mora na Rua X, 12 e o cpf é 123.456.789-00"
T2 = "Falei com a Ana ontem sobre o SAP"
T3 = "Nada a declarar, só o Excel que trava"

GOLD = {
    "S1": [gold_span(T1, "Paulo Mendes", "PESSOA"), gold_span(T1, "Rua X, 12", "LOCAL"),
           gold_span(T1, "123.456.789-00", "CPF")],
    "S2": [gold_span(T2, "Ana", "PESSOA")],
}
TEXTS = {"S1": T1, "S2": T2, "S3": T3}


def test_evaluate_counts_recall_precision_and_leaks():
    results = {
        # "Paulo" alone leaves "Mendes" uncovered; the address is caught by two pieces;
        # the CPF is caught but with the wrong type.
        "S1": result(T1, [span(T1, "Paulo", "PESSOA"), span(T1, "Rua X", "LOCAL"),
                          span(T1, "12", "LOCAL"), span(T1, "123.456.789-00", "TELEFONE")]),
        "S2": result(T2, [span(T2, "Ana", "PESSOA"), span(T2, "SAP", "PESSOA")]),
        "S3": result(T3, [span(T3, "Excel", "LOCAL")]),
    }
    m = ev.evaluate(GOLD, results, TEXTS)

    assert m["documents"] == 3 and m["documents_with_gold"] == 2
    assert m["by_gold_type"]["PESSOA"] == {
        "gold": 2, "caught": 1, "caught_same_type": 1, "recall": 0.5, "recall_typed": 0.5,
        "leaked": 0, "leak_rate": 0.0}
    assert m["by_gold_type"]["LOCAL"]["recall"] == 1.0
    assert m["by_gold_type"]["CPF"]["caught"] == 1
    assert m["by_gold_type"]["CPF"]["recall_typed"] == 0.0
    assert m["by_predicted_type"]["PESSOA"] == {"predicted": 3, "false_positives": 1,
                                                "precision": 0.6667}
    assert m["by_predicted_type"]["LOCAL"]["false_positives"] == 1
    overall = m["overall"]
    assert (overall["gold"], overall["caught"], overall["caught_same_type"]) == (4, 3, 2)
    assert overall["recall"] == 0.75 and overall["predicted"] == 7
    assert overall["false_positives"] == 2 and overall["precision"] == 0.7143
    assert m["misses"] == [{"id": "S1", "type": "PESSOA", "text": "Paulo Mendes"}]
    assert m["type_mismatches"] == [
        {"id": "S1", "type": "CPF", "text": "123.456.789-00", "predicted": ["TELEFONE"]}]
    assert {(fp["id"], fp["text"]) for fp in m["false_positives"]} == {("S2", "SAP"), ("S3", "Excel")}


def test_leak_rate_uses_whole_words_and_missing_results():
    text = "A Ana mora na Analândia"
    gold = {"S1": [gold_span(text, "Ana", "PESSOA")], "S9": [{"start": 0, "end": 3,
                                                             "type": "CPF", "text": "123"}]}
    m = ev.evaluate(gold, {"S1": result(text, [span(text, "Ana", "PESSOA")])})
    assert m["by_gold_type"]["PESSOA"]["leaked"] == 0  # "Analândia" does not count as "Ana"
    assert m["by_gold_type"]["CPF"] == {"gold": 1, "caught": 0, "caught_same_type": 0,
                                        "recall": 0.0, "recall_typed": 0.0,
                                        "leaked": 1, "leak_rate": 1.0}
    assert m["false_positives"] == []


def test_check_gold_flags_bad_offsets():
    gold = {"S1": [gold_span(T1, "Paulo Mendes", "PESSOA"),
                   {"start": 0, "end": 3, "type": "CPF", "text": "xyz"}]}
    assert ev.check_gold(gold, TEXTS) == ["S1 CPF 'xyz'"]


def test_cli_writes_metrics(tmp_path, capsys):
    responses = tmp_path / "responses.csv"
    with open(responses, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "submitted_at", "score", "area", "tenure", "comment"])
        writer.writerow(["S1", "2026-10-01T09:00:00-03:00", 3, "operacoes", "lt1", T1])
        writer.writerow(["S2", "2026-10-01T09:05:00-03:00", 9, "comercial", "gt5", T2])
        writer.writerow(["S3", "2026-10-01T09:10:00-03:00", 7, "pessoas", "y1to3", T3])
        writer.writerow(["S4", "2026-10-01T09:15:00-03:00", 10, "juridico", "y3to5", ""])
    gold = tmp_path / "pii_gold.jsonl"
    gold.write_text("\n".join(json.dumps({"id": k, "spans": v}) for k, v in GOLD.items()) + "\n",
                    encoding="utf-8")
    out = tmp_path / "metrics" / "anonymization.json"

    ev.main(["--responses", str(responses), "--gold", str(gold), "--out", str(out)])

    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert metrics["documents"] == 3  # the empty comment is not evaluated
    assert metrics["overall"]["recall"] == 1.0 and metrics["overall"]["leak_rate"] == 0.0
    assert metrics["gold_offset_problems"] == []
    assert metrics["anonymizer_version"] == "1.0.0"
    assert "ALL" in capsys.readouterr().out
