# Repository Instructions

This repository is for **benchmark authoring from a manuscript plus participant-visible starting data**.

For each case, the repo should produce:

1. a ground-truth knowledge artifact derived from the manuscript
2. a participant-facing research-question and data package
3. an evaluator-facing scoring schema derived from the ground truth

Do not silently collapse this into manuscript-to-CSAG only. The benchmark-authoring outputs are part of the default workflow.

## Entry Point

When the user provides a new benchmark case named `<case>`:

- stage the manuscript source under:
  - `ground_truth/<case>/data/<case>.pdf`
  - or `ground_truth/<case>/data/<case>.docx`
  - or `ground_truth/<case>/data/<case>.md`
- stage participant-visible starting data under:
  - `ground_truth/<case>/starting_data/`

The starting data may include local files, accession numbers, repository IDs, download instructions, or a mix of these.

When the case name is already known, treat `ground_truth/<case>/` as the only working root for that case. Do not scan the whole repository for the input file.

## Read Before Acting

Before making changes:

- read `tasks/lessons.md` if it exists
- read the active section in `tasks/todo.md`
- inspect one existing `ground_truth/<case>/` tree to match repo style
- ensure the repo uv environment is available via `uv sync`
- assume the active workflow is run via `uv run python ...`

## Required Workflow

For a staged manuscript plus starting data source:

1. keep the original manuscript under `ground_truth/<case>/data/`
2. keep participant-visible data under `ground_truth/<case>/starting_data/`
3. use the repo-local `paper-to-md` skill to create:
   - `ground_truth/<case>/data/<case>.md`
   - `ground_truth/<case>/data/<case>.section_audit.json`
   - `ground_truth/<case>/data/<case>.article.json`
4. if the paper exposes figure or table captions, use the repo-local `paper-to-md` helper to render relevant PDF pages to PNG and inspect the images directly before finalizing `ground_truth/<case>/data/<case>.article.json`
5. create `ground_truth/<case>/csag/`
6. create:
   - `ground_truth/<case>/csag/raw_paper.json`
7. use the repo-local `csag-extraction` skill to generate at least one full CSAG export:
   - `ground_truth/<case>/csag/paper_extraction.json`
   - `ground_truth/<case>/csag/paper_extraction.validation.json`
8. use the repo-local `benchmark-prompt-package` skill to generate the participant-facing prompt package:
   - `ground_truth/<case>/prompt/research_question.md`
   - `ground_truth/<case>/prompt/participant_prompt.md`
   - `ground_truth/<case>/prompt/participant_package_manifest.json`
9. use the repo-local `benchmark-scoring` skill to generate the evaluator-facing scoring package:
   - `ground_truth/<case>/scoring/scoring_schema.json`
   - `ground_truth/<case>/scoring/scoring_rubric.md`
   - `ground_truth/<case>/scoring/evaluator_instructions.md`
10. export the participant-facing subset under:
   - `ground_truth/<case>/exports/participant/`
11. export the evaluator-facing subset under:
   - `ground_truth/<case>/exports/evaluator/`

The canonical Markdown at `ground_truth/<case>/data/<case>.md` is the bridge between conversion and ground-truth extraction.
The participant prompt package must point only to participant-visible data.

## Output Contract

For a new case named `<case>`, the forward-looking minimum layout is:

```text
ground_truth/<case>/
  data/
    <case>.pdf | <case>.docx | <case>.md
    <case>.md
    <case>.section_audit.json
    <case>.article.json
  starting_data/
    manifest.yaml
    download_instructions.md
    files/...
  csag/
    raw_paper.json
    paper_extraction.json
    paper_extraction.validation.json
  prompt/
    research_question.md
    participant_prompt.md
    participant_package_manifest.json
  scoring/
    scoring_schema.json
    scoring_rubric.md
    evaluator_instructions.md
  exports/
    participant/
    evaluator/
```

If the source arrives as Markdown already, still keep it under `data/` and use that as the canonical source for ground-truth extraction.

## Skills To Use

Use these repo-local skills in this order:

1. `paper-to-md`
2. `csag-extraction`
3. `benchmark-prompt-package`
4. `benchmark-scoring`

For this repository, active workflow helper scripts belong under the owning skill.
Do not add new root-level workflow scripts when a skill-local helper is the correct home.
Treat `paper-to-md` as self-contained under `skills/paper-to-md/`.

The active workflow is intended to run from the repo's uv-managed environment, with these remaining external runtime requirements:

- access to the local OCR API service at `http://127.0.0.1:8002/ocr`
- a valid `OCR_API_KEY` or equivalent API access
- `curl` on `PATH` for the OCR helper

## Scope Limits

Unless the user explicitly asks otherwise, do **not** create or update:

- `papers/<case>.md`
- legacy root-level `<case>.prompt`
- `analysis/reference_analysis.md`
- `reproduction/`

This repo may still contain older benchmark and reproduction material. Treat that as legacy context, not the forward-looking default workflow.

## Document Conversion Rule

If the input is a PDF:

- use the repo-local `paper-to-md` skill
- keep the canonical Markdown in `ground_truth/<case>/data/<case>.md`
- do not treat the `paper-to-md` step as complete until the skill's scientific-paper sidecars exist and validate:
  - `ground_truth/<case>/data/<case>.section_audit.json`
  - `ground_truth/<case>/data/<case>.article.json`

If the input is a DOCX:

- place it in `ground_truth/<case>/data/`
- still normalize to `ground_truth/<case>/data/<case>.md`
- still complete the `paper-to-md` scientific-paper sidecars when the source is a scientific paper:
  - `ground_truth/<case>/data/<case>.section_audit.json`
  - `ground_truth/<case>/data/<case>.article.json`

Do not stop at OCR or temporary markdown. The manuscript-to-ground-truth half is only complete when the canonical Markdown, sidecars, and CSAG extraction are produced.

## Starting Data Rule

The participant-visible starting data are part of the default benchmark case.

At minimum:

- create `ground_truth/<case>/starting_data/manifest.yaml`
- create `ground_truth/<case>/starting_data/download_instructions.md`

The manifest should identify:

- which starting data are participant-visible
- local files, if any
- accessions, repository IDs, and URLs, if any
- how a participant should obtain the allowed data
- any exclusions or visibility restrictions

## CSAG Rule

Use `csag-extraction` to produce a full knowledge graph grounded in the manuscript text.

At minimum:

- keep `raw_paper.json`
- keep one full CSAG output file
- keep one validation report produced by the repo-local CSAG validator:
  - `paper_extraction.validation.json`

The CSAG extraction must also satisfy these repository rules:

- resolve `doi` and `pmid` from the staged source, OCR/article outputs, or local metadata whenever they are recoverable
- if one or both cannot be resolved, record explicit `doi_status` / `pmid_status` entries in `extraction_activities.parameters` with `resolved` or `unresolved`
- if the staged source or `paper-to-md` outputs expose figure or table captions, include `artifacts` in `paper_extraction.json`
- if the staged source exposes data-availability text, repository links, accessions, or project identifiers, include `datasets` in `paper_extraction.json`
- run the repo-local validator at `skills/csag-extraction/scripts/validate_paper_extraction.py` before stopping

The extracted graph should be derived from the canonical Markdown or the staged text source, not from memory.

## Prompt Package Rule

The participant-facing package must include:

- a research question
- a participant prompt
- only participant-visible starting data references

The participant-facing package must **not** include:

- the manuscript PDF or canonical Markdown unless the user explicitly wants them exposed
- `csag/raw_paper.json`
- `csag/paper_extraction.json`
- the scoring schema
- any hidden evaluator notes

The participant prompt should clearly tell a future research agent:

- the research question
- which data they may use
- how to obtain those data
- that they should produce an independent manuscript PDF as their final output

## Scoring Rule

The scoring package must be derived from the ground-truth knowledge artifact.

At minimum:

- create a machine-readable `scoring_schema.json`
- create a human-readable `scoring_rubric.md`
- create evaluator instructions that describe the downstream evaluation flow

The scoring schema should support:

- weighted claim coverage
- evidence and method alignment
- required entities and datasets
- quantitative-result fidelity when available
- limitations or uncertainty capture
- hallucination or contradiction penalties

## Evaluation Flow Rule

This repo is for benchmark authoring, not for running the participant agents.

The intended downstream flow is:

1. author this case in this repo
2. export only the participant-facing package
3. run a separate participant agent outside this repo
4. have that participant agent produce a manuscript PDF
5. run a separate evaluator agent to convert the participant manuscript to a candidate knowledge artifact
6. score that candidate artifact against the ground truth using the scoring package from this repo

## Verification

Before stopping, confirm:

- the source PDF or DOCX is stored under `ground_truth/<case>/data/`
- `ground_truth/<case>/starting_data/manifest.yaml` exists
- `ground_truth/<case>/starting_data/download_instructions.md` exists
- `ground_truth/<case>/data/<case>.md` exists
- `ground_truth/<case>/data/<case>.section_audit.json` exists
- `ground_truth/<case>/data/<case>.article.json` exists
- `ground_truth/<case>/csag/raw_paper.json` exists
- `ground_truth/<case>/csag/` contains a full CSAG extraction
- `ground_truth/<case>/csag/paper_extraction.validation.json` exists and reports success
- `ground_truth/<case>/prompt/research_question.md` exists
- `ground_truth/<case>/prompt/participant_prompt.md` exists
- `ground_truth/<case>/scoring/scoring_schema.json` exists
- `ground_truth/<case>/scoring/scoring_rubric.md` exists
- `ground_truth/<case>/exports/participant/` excludes ground-truth-only assets
- `ground_truth/<case>/exports/evaluator/` contains the scoring assets
- `tasks/todo.md` reflects the work you completed
