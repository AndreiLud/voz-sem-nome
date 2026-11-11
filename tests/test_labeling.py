import csv

from enps.evaluation import labeling


def _write(path, rows, fields):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_dropdowns_follow_taxonomy():
    dd = labeling.dropdowns()
    assert dd["tema_1"][0] == "Liderança"
    assert dd["tema_1"][-1] == "Outro"
    assert dd["sentimento"] == ["Positivo", "Neutro", "Negativo"]
    assert "Burnout e saúde mental" in dd["tipo_critico_1"]


def test_import_maps_labels_to_codes_and_reports_problems(tmp_path):
    sheet = tmp_path / "sheet.csv"
    base = {c: "" for c in labeling.SHEET_COLUMNS}
    _write(sheet, [
        base | {"id": "S0002", "comentario": "x", "tema_1": "Liderança", "tema_2": "carga de trabalho",
                "sentimento": "Negativo", "critico": "Sim", "tipo_critico_1": "Assédio moral"},
        base | {"id": "S0001", "comentario": "y", "tema_1": "Outro", "tema_2": "Reconhecimento",
                "sentimento": "neutro", "critico": "Não", "tipo_critico_1": "Ameaça ou violência"},
        base | {"id": "S0003", "comentario": "z", "sentimento": "Positivo", "critico": "Sim"},
    ], labeling.SHEET_COLUMNS)
    rows, problems = labeling.import_sheet(sheet, tmp_path / "out.csv")
    by_id = {r["id"]: r for r in rows}
    assert by_id["S0002"] == {"id": "S0002", "themes": "lid|car", "sentiment": "neg", "critical": "1",
                              "critical_types": "am", "doubt": "0"}
    # a critical type wins over "Não", and the inconsistency is reported
    assert by_id["S0001"]["critical"] == "1"
    assert any("S0001" in p and "Outro" in p for p in problems)
    assert any("S0001" in p and "critico" in p for p in problems)
    assert any("S0003" in p and "no theme" in p for p in problems)
    assert any("S0003" in p and "without a type" in p for p in problems)
    with open(tmp_path / "out.csv", encoding="utf-8") as fh:
        assert [r["id"] for r in csv.DictReader(fh)] == ["S0001", "S0002", "S0003"]
