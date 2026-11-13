# How the numbers were measured

Every number on the site and in the README comes from code in this repository and is stored under `metrics/`. The
one exception is the labels of the blind model panel (below): they were made outside this code and are stored in
`data/labels/panel/`, and `src/enps/evaluation/panel.py` recomputes their vote and agreement from the stored readings.
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

* **Blind model panel** (main reference for now, `data/labels/panel_test.csv`). The project owner chose not to label the
  test split by hand, so a different language model, from a provider other than the ones the system uses, labeled the
  100 test comments blind:
  * input: the same blind sheet a person would get (`data/labels/labeling_sheet.csv`: the anonymized text only, in
    shuffled order, with no model prediction and no generator label), with neutral ids (C001 to C100), and the same
    labeling guide (the taxonomy of `config/taxonomy.yaml` with its rules);
  * three independent readings, each in its own session that could open only its batch file, in three different
    orders, in batches of 50;
  * themes, sentiment, the critical flag and the critical types are set by majority vote (2 of 3); the doubt flag is
    set when any reading had a doubt; a comment without a majority on some field would go to one more reading that
    saw the three answers and decided (`settled_by` in `data/labels/panel/readings.json`, which also keeps every
    reading).

  The three readings agreed on 97 of 100 sentiments (Fleiss kappa 0.96) and 99 of 100 critical flags (0.98), and no
  comment needed the extra reading (`panel_readings` in `metrics/classification.json`, which also checks that every
  final label is the vote of the readings). Against the generator labels, the panel matches all 20 critical cases and 88% of
  the sentiments, with theme micro F1 0.92 (`panel_vs_intended` in `metrics/classification.json`).

  These are **not human labels**. The three readings come from the same model, so their mistakes can be correlated,
  and a model reference can share blind spots with the models it judges. The numbers measure agreement with an
  independent automatic reading, nothing more.
* **Human labels** (`data/labels/human_test.csv`, not done yet). The blind sheet and its guide stay ready
  (`src/enps/evaluation/labeling.py`). When the file exists, the evaluation and the site use it as the main reference
  automatically; the panel results stay in `metrics/classification.json` and `metrics/edge/classification_test.json`.
* **Generator labels** (weaker, `intended_labels.csv`): the themes, sentiment and critical flag the generator intended
  for each comment. They are always reported separately, because comments are composed from labeled fragments and the
  composition can shift the meaning.

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
  comment, tokens, and an estimate of the Workers AI allocation used. The speed reported for the demo comes from the
  first 300 comments (`benchmark300`), sent one at a time and spread over two days to stay inside the daily free
  allocation.

## Use in production

Counts from the live demo (submissions, distinct people per day, time from submission to result, critical cases and
alerts) are read from D1 by `GET /api/v1/public/status` and shown on the dashboard. They are never mixed with the
synthetic numbers.

They start at `vars.PUBLIC_START_DAY` in `wrangler.jsonc` (2026-11-12). The 6 answers sent on 2026-11-11 were the
author's tests of the deployed demo from a phone, so they are left out of the usage numbers and of the public dashboard.
They stay in D1 until the 30-day purge, and each one can still be looked up by its tracking code.
