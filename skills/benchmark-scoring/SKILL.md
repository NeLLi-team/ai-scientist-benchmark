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
