---
name: benchmark-prompt-package
description: Build the participant-facing benchmark package for one case from the ground-truth artifact and the staged starting-data manifest. Produces the research question, participant prompt, package manifest, and participant export.
---

# Benchmark Prompt Package

Use this skill when a benchmark-authoring case already has:

- a staged manuscript under `ground_truth/<case>/data/`
- participant-visible starting data under `ground_truth/<case>/starting_data/`
- a ground-truth CSAG artifact under `ground_truth/<case>/csag/`

and the repo needs the **participant-facing package**.

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

- `ground_truth/<case>/prompt/research_question.md`
- `ground_truth/<case>/prompt/participant_prompt.md`
- `ground_truth/<case>/prompt/participant_package_manifest.json`
- `ground_truth/<case>/exports/participant/`

The participant export must contain only participant-visible assets.

## Core rule

The participant package must **not** expose:

- the manuscript PDF unless explicitly requested
- `csag/raw_paper.json`
- `csag/paper_extraction.json`
- scoring assets
- hidden evaluator notes

## Script

Use the helper script:

```bash
uv run python skills/benchmark-prompt-package/scripts/build_prompt_package.py \
  /abs/path/to/ground_truth/<case> \
  --research-question "..."
```

If `--research-question` is omitted, the helper will try to derive one from the objective assertion or title, but a curated question is preferred.

## Blinded ground truth

`blind_case.py` makes a blinded copy of a case and reuses the source ground truth. The scoring judges then see names that a participant cannot find in the provided data. `blind_ground_truth.py` writes a blinded ground truth for a case that `blind_case.py` already made:

```bash
uv run python skills/benchmark-prompt-package/scripts/blind_ground_truth.py \
  --source-case ground_truth/<source> --blind-case ground_truth/<blinded> \
  --rewrites rewrites.json --terms-file terms.txt
```

- `--rewrites` is a JSON file: `{"assertions": {"A0001": {"assertion_text": "...", "falsification_criteria": ["..."]}}, "evidence_items": {"E0004": {"evidence_text": "..."}}}`. Keys are the record `label` values. An evidence item without a `label` uses the last segment of its `id`. Each entry may set any of the fields shown. The script refuses unknown labels and fields.
- `--terms-file` holds one forbidden term per line. Blank lines and lines that start with `#` are ignored. The format matches `audit_leakage.py`.
- After the rewrites, the script searches the judge-visible fields for every term, ignoring case. These fields are `assertion_text`, `falsification_criteria` and `evidence_text`. If a term remains, it writes nothing and exits with code 2. It lists each hit as `<label>.<field>: <term>`. `text_spans` are not checked, because only the human reviewer sees them.
- On success it writes `<blind-case>/csag/paper_extraction.json` (the source with the rewrites applied, all other fields and ids unchanged) and copies the scoring schema to `<blind-case>/scoring/scoring_schema.json`.
- It replaces the `reused_ground_truth` block in `<blind-case>/case.yaml`, or the block of an earlier run, with a `blinded_ground_truth` block. The block holds the path and SHA-256 of the source CSAG, the rewrites file, the output CSAG and the scoring schema.
