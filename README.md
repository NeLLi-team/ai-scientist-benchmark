# Advanced Benchmark

This repository is an internal **benchmark-authoring repo** for scientific AI evaluation.

For each case, the authoring workflow starts from:

1. a source manuscript PDF
2. the participant-visible starting data or explicit download instructions

and produces three outputs:

1. a ground-truth knowledge artifact
2. a participant-facing research-question and data package
3. an evaluator-facing scoring schema derived from the ground truth

The participant agents that later attempt the research do **not** work from this repo and do **not** see the ground-truth artifact.

## Tracked Code And Local Data

The repository tracks workflow code, schemas, tests, and public documentation. Benchmark case data and local reference material are intentionally ignored:

- `ground_truth/*`
- `06mega/`
- `refs/`
- `tasks/`

Use those ignored locations for private manuscripts, generated case artifacts, local reference papers, and working notes. Do not rely on ignored local files being available in another checkout.

## What The User Must Provide

For a new case named `<case>`, the user should provide:

- the manuscript or publication source file under `ground_truth/<case>/data/`
- the participant-visible starting data under `ground_truth/<case>/starting_data/`

The starting data can include any mix of:

- local files such as assemblies, transcriptomes, annotations, or tables
- accession numbers that can be downloaded later
- repository IDs or project IDs
- download instructions for data that participants are allowed to use

The manuscript should start from PDF so figure pages can be rendered and reviewed during artifact generation.
The manuscript is used to generate the ground-truth knowledge artifact.
The starting data are used to build the participant package and must be safe to expose to future participant agents.

## What This Repo Generates

For each case, this repo should generate:

- `csag/`
  - the ground-truth CSAG knowledge artifact and validation report
- `prompt/`
  - a research question
  - a participant prompt that points only to participant-visible data
- `scoring/`
  - a machine-readable scoring schema
  - a human-readable scoring rubric
  - evaluator instructions
- `exports/participant/`
  - the participant-facing package only
- `exports/evaluator/`
  - the evaluator-facing package only

## Runtime Setup

From the repo root, the active workflow should be run from the uv-managed environment:

```bash
uv sync
```

Then run helper scripts with:

```bash
uv run python ...
```

Current non-repo runtime dependencies that still remain by design:

- access to the local OCR API service at `http://127.0.0.1:8002/ocr`
- a valid `OCR_API_KEY` or equivalent API access
- `curl` on `PATH` for the OCR helper

The PDF page renderer used for figure review is now Python-based and comes from the uv-managed environment rather than a system `pdftoppm` dependency.

## Case Layout

The intended forward-looking layout for one case is:

```text
ground_truth/<case>/
  data/
    <case>.pdf
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

Notes:

- `data/` is the manuscript-to-ground-truth path.
- `starting_data/` is the participant-visible scientific starting point.
- `csag/` is internal ground truth.
- `csag/paper_extraction.json` uses CSAG's assertion/evidence spine. Ground-truth assertions should include `criticality` and `falsification_criteria`.
- `prompt/` and `exports/participant/` are what future participant agents should receive.
- `scoring/` and `exports/evaluator/` are what the evaluator should receive.

Some older files in the repository still reflect an earlier benchmark or reproduction workflow. Treat them as legacy context unless you are explicitly migrating them.

## Authoring Workflow

For a new case:

1. stage the manuscript under `ground_truth/<case>/data/`
2. stage participant-visible starting data under `ground_truth/<case>/starting_data/`
3. run `paper-to-md` from the uv-managed environment
   - for scientific papers with recoverable figures or tables, render relevant PDF pages to PNG and use them to populate `figure_interpretation`
4. run `csag-extraction` from the uv-managed environment
   - validate ground-truth CSAG artifacts with `--profile ground_truth`
5. generate the participant prompt package from the uv-managed environment
6. generate the scoring schema package from the uv-managed environment
7. export participant-facing and evaluator-facing subsets

The expected repo-local skill order is:

1. `paper-to-md`
2. `csag-extraction`
3. `benchmark-prompt-package`
4. `benchmark-scoring`

Active workflow helper scripts should live under the owning skill directory.
Do not treat a root-level `scripts/` directory as the default place for new workflow logic.
The `paper-to-md` skill should be self-contained under `skills/paper-to-md/`.

## CSAG Validation Profiles

The CSAG validator is:

```bash
uv run python skills/csag-extraction/scripts/validate_paper_extraction.py ...
```

It supports two profiles:

- `candidate` is the default. It preserves compatibility with participant or evaluator-generated candidate CSAG artifacts.
- `ground_truth` is stricter and should be used for benchmark answer artifacts under `ground_truth/<case>/csag/`.

The `ground_truth` profile requires stronger structural integrity:

- stable unique IDs
- resolved internal references
- evidence links with `polarity`, `strength`, and `rationale`
- assertion `criticality`
- assertion `falsification_criteria`
- evidence links for non-background assertions
- decisive support/refutation for non-background assertions
- text-span grounding for non-background assertions, either on the assertion or linked evidence item
- at least moderate decisive evidence for core and major assertions, unless the assertion is explicitly a limitation or speculation

For a case ground truth, use:

```bash
uv run python skills/csag-extraction/scripts/validate_paper_extraction.py \
  ground_truth/<case>/csag/paper_extraction.json \
  --profile ground_truth \
  --source-markdown ground_truth/<case>/data/<case>.md \
  --article-json ground_truth/<case>/data/<case>.article.json \
  --report-out ground_truth/<case>/csag/paper_extraction.validation.json
```

## Scoring Metadata

The scoring package is derived from the ground-truth CSAG. Assertion checks include:

- `criticality`
- `falsification_criteria`
- linked evidence IDs
- evidence strengths
- required evidence strength
- a required flag
- a weight multiplier

When `criticality` is present, scoring weights are derived from it:

- `core`: 2.0
- `major`: 1.5
- `supporting`: 1.0
- `background`: 0.5

If `criticality` is absent, the scorer falls back to the older claim-role weighting so older artifacts remain usable.

## Authoring Agent Prompt

Use a prompt like this from the repo root:

```text
Use the repo-local AGENTS.md.

Case name: <case>

The manuscript source is staged at:
ground_truth/<case>/data/<case>.pdf

The participant-visible starting data are staged at:
ground_truth/<case>/starting_data/

Run the repo-local workflow to create:
- canonical Markdown plus paper-to-md sidecars
- a ground-truth CSAG knowledge artifact plus validation report
- a participant-facing research question and prompt package that only points to participant-visible data
- an evaluator-facing scoring schema package derived from the ground-truth artifact

Do not include the manuscript PDF, CSAG ground truth, or scoring files in the participant export.
Do not assume participant agents can see this repository.
```

## Downstream Evaluation Flow

The intended benchmark loop is:

1. This repo authors a case from manuscript + starting data.
2. A participant agent outside this repo receives only the participant export.
3. The participant agent performs independent research from the starting data and writes a manuscript PDF.
4. A separate evaluator agent independently converts that candidate manuscript PDF into a candidate knowledge artifact.
5. The evaluator applies the scoring schema from this repo to compare the candidate artifact against the ground-truth artifact.

This repo is therefore for **authoring ground truth, participant prompts, and scoring assets**, not for running the participant research agents themselves.

## Acceptance Checklist

A case is ready when:

- the manuscript source exists under `ground_truth/<case>/data/`
- the participant-visible data manifest exists under `ground_truth/<case>/starting_data/`
- the canonical Markdown and `paper-to-md` sidecars exist
- the CSAG artifact and ground-truth-profile validation report exist
- ground-truth assertions include criticality and falsification criteria
- the participant prompt package exists
- the scoring schema package exists
- the participant export excludes ground-truth-only assets
- the evaluator export includes the scoring assets needed for comparison

## Tests

Run the repository tests with:

```bash
uv run python -m unittest discover -s tests
```
