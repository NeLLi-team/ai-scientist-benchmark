---
name: benchmark-scoring
description: Build the evaluator-facing scoring package for one case from the ground-truth CSAG artifact. Produces a machine-readable scoring schema, human-readable rubric, evaluator instructions, and evaluator export.
---

# Benchmark Scoring

Use this skill when a benchmark-authoring case already has:

- a ground-truth CSAG artifact under `ground_truth/<case>/csag/`
- a participant research question under `ground_truth/<case>/prompt/`
- a starting-data manifest under `ground_truth/<case>/starting_data/`

and the repo needs the **evaluator-facing scoring package**.

## Runtime setup

From the repo root:

```bash
uv sync
```

Run the helper with:

```bash
uv run python ...
```

## Required outputs

For one case `<case>`, produce:

- `ground_truth/<case>/scoring/scoring_schema.json`
- `ground_truth/<case>/scoring/scoring_rubric.md`
- `ground_truth/<case>/scoring/evaluator_instructions.md`
- `ground_truth/<case>/exports/evaluator/`

## Core rule

The scoring package is derived from the ground-truth knowledge artifact and is not participant-visible.

## Script

Use the helper script:

```bash
uv run python skills/benchmark-scoring/scripts/build_scoring_package.py \
  /abs/path/to/ground_truth/<case>
```

## Scoring candidate CSAGs

`scripts/score_csag_candidates.py` scores candidate `*_csag.json` files (with their `*_csag.validation.json`) against a case's scoring schema and writes ranked tables:

```bash
uv run python skills/benchmark-scoring/scripts/score_csag_candidates.py \
  --case-dir ground_truth/<case> \
  --candidate-dir <dir-of-candidate-csags> \
  --out-dir <output-dir>
```

Known limits: its claim, method, entity, dataset, quantitative and limitation rules are hand-written for `06mega`, and it matches keywords as substrings over the flattened candidate CSAG text. Short tokens can match inside other words and negated statements still earn credit, so treat totals as a ranking rather than calibrated accuracy. Blinded candidates must be mapped back to original identifiers before scoring, because the dataset and entity rules look for the original IMG/M IDs.
