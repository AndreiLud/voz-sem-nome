# How the numbers were measured

Every number on the site and in the README comes from code in this repository and is stored under `metrics/`.
A number that was not measured yet is shown as such. Estimates (the hours saved) are always labeled as estimates.

## Data and splits

* 400 synthetic answers from a fictitious company (`data/synthetic/`, see [synthetic-data.md](synthetic-data.md)),
  340 of them with a comment. Seed 20261111; the generator is deterministic.
* `splits.json`:
  * **test**: 100 comments, stratified so that critical cases (20, every type at least 3), near misses and rare themes
    are present. Used only to report results. No prompt or rule was changed after looking at it.
  * **dev**: the other 240 comments with text, used to choose the model and tune prompts and rules.
  * **dev_quick**: 40 comments from dev, used for the first model comparison.

## References

* **Human labels** (primary): the project owner labeled the 100 test comments in a blind sheet: the anonymized text
  only, in shuffled order, with no model prediction and no generator label (`src/enps/evaluation/labeling.py`). The
  guide given to the labeler is the taxonomy in `config/taxonomy.yaml`.
* **Generator labels** (weaker): the themes, sentiment and critical flag the generator intended for each comment
  (`intended_labels.csv`). They are reported separately and only as a secondary reference, because comments are
  composed from labeled fragments and the composition can shift the meaning.

## Classification metrics

`src/enps/evaluation/classification.py`:
* Sentiment: exact agreement, Cohen's kappa and the 3 x 3 confusion matrix.
* Themes (multi-label, up to 4 per comment): precision, recall and F1 per theme with support, plus micro and macro F1.
* Critical flag: recall and precision with TP, FP and FN counts and Wilson 95% intervals, and recall per type.
  Recall comes first: a false alarm costs minutes, a missed case costs much more.
* Critical flag by configuration, rebuilt from the recorded stages: the model alone, the model plus the risk lexicon,
  and the full pipeline with the second reading. This shows whether each step earns its cost.

## Prompt history (dev set only)

| Prompt | Change | Measured on |
|---|---|---|
| classify.v1 | first version, excerpt before the decision | dev_quick, Qwen3.5 2B and Gemma 4 E2B |
| classify.v2 | decide before quoting; "out" mixed with other themes repaired | dev, Gemma 4 E2B |
| classify.v3 | explicit critical signals, common complaints listed as not critical, three near-miss examples; a strong lexicon word is ignored only when both model readings say no | dev, Gemma 4 E2B |

The files are `metrics/model_comparison.json`, `metrics/dev_round_v2.json` and `metrics/dev_round_v3.json`. Each
classification record stores the prompt version, which includes a hash of the exact rendered prompt.

## Anonymization

`src/enps/evaluation/anonymization.py` (Python) and `scripts/evaluate_worker_anonymizer.mjs` (the JavaScript version
that runs in the Cloudflare Worker) compare the output with `pii_gold.jsonl`, which records the position and type of
every fake personal item planted by the generator.
* A planted item counts as removed when every letter and digit of it is covered by masked spans.
* Recall per type is the number that matters for privacy. Precision counts masks that hit no planted item.
* Leak rate: share of planted items still present as whole words in the output.
* Earlier versions are kept in `metrics/history/`. Version 1.1.0 added the official IBGE lists of municipalities,
  districts and subdistricts; no rule was written for a specific miss of the synthetic file.

## Performance

Two deployments, measured separately:
* **Local mode** (`python -m enps.pipeline classify`, `metrics/benchmark.json`): the synthetic comments go through the
  real queue worker and Ollama on the reference server (a VM with 6 vCPUs and SSE4.2 only, no GPU). The first comment
  after a cold start (model load and full prompt read) is reported apart from the steady state of the next 300:
  total, mean, p50, p95, tokens per second, model, quantization and hardware.
* **Cloudflare demo** (`python -m enps.evaluation.edge_client`, `metrics/edge/`): the same comments sent through the
  deployed Worker (anonymization in JavaScript, then Workers AI). Latency of the model part and end to end per
  comment, tokens, and an estimate of the Workers AI allocation used.

## Use in production

Counts from the live demo (submissions, distinct people per day, time from submission to result, critical cases and
alerts) are read from D1 by `GET /api/v1/public/status` and shown on the dashboard. They start at the day the demo went
live and are never mixed with the synthetic numbers.
