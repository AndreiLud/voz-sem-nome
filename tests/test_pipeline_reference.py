"""Which labels each mode's headline numbers are compared with."""
from __future__ import annotations

from enps import pipeline
from enps.export.site_data import reference_kind
from enps.export.site_content import load_sources


def test_human_labels_win_then_the_panel_then_the_generator():
    human, panel, intended = {"reference": "human"}, {"reference": "panel"}, {"reference": "intended"}
    edge = {"human": human, "panel": panel, "intended": intended}
    assert pipeline.preferred(edge, pipeline.EDGE_REFERENCES) is human
    assert pipeline.preferred({**edge, "human": None}, pipeline.EDGE_REFERENCES) is panel
    assert pipeline.preferred({"intended": intended, "panel": None}, pipeline.EDGE_REFERENCES) is intended
    local = {"human_test": None, "panel_test": panel, "intended_test": intended}
    assert pipeline.preferred(local, pipeline.LOCAL_REFERENCES) is panel
    assert pipeline.preferred({}, pipeline.LOCAL_REFERENCES) is None


def test_the_site_names_every_reference_kind():
    for ref in ("intended", "panel", "human"):
        assert reference_kind({"classification": {"reference": ref}}) == ref
    assert reference_kind({"classification": {"reference": "other"}}) == "intended"
    assert reference_kind({}) is None
    sources = load_sources()
    for lang in ("pt", "en"):
        notes = sources.strings[lang]["home"]["results"]["reference"]
        assert set(notes) == {"intended", "panel", "human"}
    assert "Não são rótulos humanos" in sources.strings["pt"]["home"]["results"]["reference"]["panel"]
    assert "not human labels" in sources.strings["en"]["home"]["results"]["reference"]["panel"]
