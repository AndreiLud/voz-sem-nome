import csv
import json

import pytest

from enps.evaluation.classification import (
    Labels, cohen_kappa, confusion_matrix, evaluate, main, prf, wilson_interval,
)


def lab(themes=("lid",), sentiment="neg", critical=False, types=()):
    return Labels(frozenset(themes), sentiment, critical, frozenset(types))


def test_kappa_matches_a_hand_computed_example():
    # n=30, observed 22/30, expected (12*11 + 8*10 + 10*9) / 900 = 302/900
    matrix = [[10, 2, 0], [1, 5, 2], [0, 3, 7]]
    assert cohen_kappa(matrix) == pytest.approx(358 / 598)
    assert cohen_kappa([[20, 5], [10, 15]]) == pytest.approx(0.4)
    assert cohen_kappa([[5, 0], [0, 5]]) == pytest.approx(1.0)
    assert cohen_kappa([[0, 0], [0, 0]]) is None
    assert cohen_kappa([[4, 0], [0, 0]]) is None


def test_confusion_rows_are_reference():
    assert confusion_matrix(["pos", "neg", "neg"], ["neg", "neg", "neu"], ["pos", "neu", "neg"]) == \
        [[0, 0, 1], [0, 0, 0], [0, 1, 1]]


def test_prf():
    assert prf(8, 2, 4) == pytest.approx({"precision": 0.8, "recall": 8 / 12, "f1": 2 * 0.8 * (8 / 12) / (0.8 + 8 / 12)})
    assert prf(0, 0, 3) == {"precision": None, "recall": 0.0, "f1": 0.0}
    assert prf(0, 0, 0) == {"precision": None, "recall": None, "f1": None}


def test_wilson_interval():
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4) and high == pytest.approx(0.9433, abs=1e-4)
    assert wilson_interval(0, 0) == (None, None)
    low, high = wilson_interval(10, 10)
    assert high == pytest.approx(1.0) and low == pytest.approx(0.7225, abs=1e-4)


def test_evaluate_counts_everything():
    ref = {
        "S1": lab(("lid",), "neg", True, ("am",)),
        "S2": lab(("rem",), "pos"),
        "S3": lab(("bem", "car"), "neg", True, ("bo",)),
        "S4": lab(("out",), "neu"),
        "S5": lab(("inf",), "neg"),
    }
    pred = {
        "S1": lab(("lid",), "neg", True, ("am",)),
        "S2": lab(("rem",), "neu"),
        "S3": lab(("bem",), "neg", False),
        "S4": lab(("out",), "neu", True, ("bo",)),
    }
    report = evaluate(ref, pred)
    assert report["n"] == 4 and report["missing_predictions"] == ["S5"]
    crit = report["critical"]
    assert (crit["tp"], crit["fp"], crit["fn"], crit["tn"]) == (1, 1, 1, 1)
    assert crit["recall"] == 0.5 and crit["precision"] == 0.5
    assert crit["by_type"]["am"]["recall_type"] == 1.0
    assert crit["by_type"]["bo"]["recall_flag"] == 0.0 and crit["by_type"]["bo"]["support"] == 1
    assert crit["by_type"]["di"]["support"] == 0 and crit["by_type"]["di"]["recall_flag"] is None

    sent = report["sentiment"]
    assert sent["accuracy"] == 0.75 and sent["labels"] == ["pos", "neu", "neg"]
    assert sent["confusion"] == [[0, 1, 0], [0, 1, 0], [0, 0, 2]]

    themes = report["themes"]
    assert themes["per_theme"]["car"] == {"precision": None, "recall": 0.0, "f1": 0.0,
                                          "support": 1, "tp": 0, "fp": 0, "fn": 1}
    assert themes["per_theme"]["lid"]["f1"] == 1.0
    # themes with support: lid, rem, bem, car, out -> F1 1, 1, 1, 0, 1
    assert themes["macro_f1"] == pytest.approx(0.8)
    assert themes["micro_f1"] == pytest.approx(2 * 4 / (2 * 4 + 0 + 1))

    kinds = {e["id"]: e["kinds"] for e in report["errors"]}
    assert kinds == {"S3": ["critical_fn", "themes"], "S4": ["critical_fp"], "S2": ["sentiment"]}
    assert [e["id"] for e in report["errors"]] == ["S3", "S4", "S2"]


def test_missing_sentiment_prediction_counts_as_wrong():
    report = evaluate({"S1": lab(sentiment="neg"), "S2": lab(sentiment="pos")},
                      {"S1": lab(sentiment=None), "S2": lab(sentiment="pos")})
    assert report["sentiment"]["accuracy"] == 0.5
    assert report["sentiment"]["missing_prediction"] == 1


def test_cli_reads_files_and_split(tmp_path, capsys):
    ref_path = tmp_path / "intended_labels.csv"
    with open(ref_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "themes", "sentiment", "critical", "critical_types", "subtle", "traits"])
        writer.writerow(["S0001", "lid|car", "neg", "1", "am", "1", "irony"])
        writer.writerow(["S0002", "rem", "pos", "0", "", "0", ""])
        writer.writerow(["S0003", "", "", "0", "", "0", "empty"])
        writer.writerow(["S0004", "inf", "neg", "0", "", "0", ""])
    pred_path = tmp_path / "pred.jsonl"
    pred_path.write_text("\n".join(json.dumps(r) for r in [
        {"id": "S0001", "themes": ["lid"], "sentiment": "neg", "critical": False, "critical_types": []},
        {"id": "S0002", "themes": "rem", "sentiment": "pos", "critical": 0, "critical_types": ""},
        {"id": "S0004", "themes": ["inf"], "sentiment": "neg", "critical": True, "critical_types": ["av"]},
    ]) + "\n", encoding="utf-8")
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"seed": 1, "test": ["S0001", "S0002", "S0003"], "dev": []}))
    out = tmp_path / "out" / "metrics.json"

    assert main(["--pred", str(pred_path), "--ref", str(ref_path), "--ids", "test",
                 "--splits", str(splits), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["n"] == 2 and report["ids"] == "test"
    assert report["critical"]["fn"] == 1 and report["critical"]["fp"] == 0
    assert report["errors"][0]["id"] == "S0001"
    assert report["errors"][0]["traits"] == "irony" and report["errors"][0]["subtle"] == "1"
    assert "critical_recall=0.0" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main(["--pred", str(pred_path), "--ref", str(ref_path), "--ids", "nope",
              "--splits", str(splits), "--out", str(out)])
