"""Agreement between the panel readings and the majority vote."""
from __future__ import annotations

import json

import pytest

from enps.evaluation import panel


def reading(temas, sentimento, critico=False, tipos=(), duvida=False):
    return {"temas": list(temas), "sentimento": sentimento, "critico": critico, "tipos": list(tipos), "duvida": duvida}


def test_fleiss_kappa_matches_a_hand_computed_case():
    # P_bar = (1 + 1/3 + 1 + 1) / 4, P_e = (5/12)^2 + (7/12)^2, kappa = (P_bar - P_e) / (1 - P_e)
    rows = [["x", "x", "x"], ["x", "x", "y"], ["y", "y", "y"], ["y", "y", "y"]]
    assert panel.fleiss_kappa(rows) == pytest.approx((10 / 12 - 74 / 144) / (1 - 74 / 144))
    assert panel.fleiss_kappa([["x", "x"], ["x", "x"]]) is None  # one category only: undefined
    assert panel.fleiss_kappa([]) is None
    with pytest.raises(ValueError):
        panel.fleiss_kappa([["x", "y"], ["x"]])


def test_majority_votes_labels_and_keeps_any_doubt():
    vote = panel.majority([
        reading(["Liderança", "Carga de trabalho"], "Negativo", True, ["Assédio moral"]),
        reading(["Liderança"], "Negativo", True, ["Assédio moral", "Burnout e saúde mental"], duvida=True),
        reading(["Carga de trabalho", "Outro"], "Neutro"),
    ])
    assert vote == {"temas": {"Liderança", "Carga de trabalho"}, "sentimento": "Negativo", "critico": True,
                    "tipos": {"Assédio moral"}, "duvida": True}
    split = panel.majority([reading(["Outro"], "Positivo"), reading(["Outro"], "Neutro"), reading(["Liderança"], "Negativo")])
    assert split["sentimento"] is None and split["temas"] == {"Outro"} and split["tipos"] == set()


def test_summary_counts_agreement_and_checks_the_final_labels(tmp_path):
    a = {"S1": reading(["Liderança"], "Negativo", True, ["Assédio moral"]), "S2": reading(["Outro"], "Positivo")}
    b = {"S1": reading(["Liderança"], "Negativo", True, ["Assédio moral"]), "S2": reading(["Outro"], "Neutro")}
    c = {"S1": reading(["Liderança"], "Neutro", True, ["Assédio moral"]), "S2": reading(["Outro"], "Positivo", duvida=True)}
    final = {
        "S1": {**reading(["Liderança"], "Negativo", True, ["Assédio moral"]), "settled_by": "majority"},
        "S2": {**reading(["Outro"], "Positivo", duvida=True), "settled_by": "majority"},
    }
    path = tmp_path / "readings.json"
    path.write_text(json.dumps({"final": final, "readings": {"A": a, "B": b, "C": c}}), encoding="utf-8")
    result = panel.summary(path)
    assert result["n"] == 2 and result["readers"] == ["A", "B", "C"]
    assert result["unanimous"] == {"sentiment": 0, "critical": 2}
    assert result["fleiss_kappa"]["critical"] == 1.0  # all three agree on both comments
    assert result["fleiss_kappa"]["sentiment"] == pytest.approx(0)  # one dissent on each comment: chance level
    assert result["final_matches_vote"] is True and result["vote_mismatches"] == []
    final["S2"]["sentimento"] = "Neutro"
    path.write_text(json.dumps({"final": final, "readings": {"A": a, "B": b, "C": c}}), encoding="utf-8")
    assert panel.summary(path)["vote_mismatches"] == ["S2"]
