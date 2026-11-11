"""Synthetic survey answers for the fictitious company "Talvana Distribuição".

`python -m enps.synth` writes responses.csv, intended_labels.csv, pii_gold.jsonl and
splits.json to data/synthetic/. Comments are composed from the curated phrase bank in
data/synthetic/phrasebank.yaml; see docs/synthetic-data.md.
"""
from .generator import Dataset, generate, write_dataset
from .story import DEFAULT_SEED

__all__ = ["DEFAULT_SEED", "Dataset", "generate", "write_dataset"]
