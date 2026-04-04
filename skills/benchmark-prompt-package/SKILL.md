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
python skills/benchmark-prompt-package/scripts/build_prompt_package.py \
  /abs/path/to/ground_truth/<case> \
  --research-question "..."
```

If `--research-question` is omitted, the helper will try to derive one from the objective assertion or title, but a curated question is preferred.
