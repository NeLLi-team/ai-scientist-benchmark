# Advanced Benchmark

This repository is an internal **benchmark-authoring repo** for scientific AI evaluation.

For each case, the authoring workflow starts from:

1. a source manuscript PDF, DOCX, or Markdown
2. the participant-visible starting data or explicit download instructions

and produces three outputs:

1. a ground-truth knowledge artifact
2. a participant-facing research-question and data package
3. an evaluator-facing scoring schema derived from the ground truth

The participant agents that later attempt the research do **not** work from this repo and do **not** see the ground-truth artifact.

## What The User Must Provide

For a new case named `<case>`, the user should provide:

- the manuscript or publication source file under `ground_truth/<case>/data/`
- the participant-visible starting data under `ground_truth/<case>/starting_data/`

The starting data can include any mix of:

- local files such as assemblies, transcriptomes, annotations, or tables
- accession numbers that can be downloaded later
- repository IDs or project IDs
- download instructions for data that participants are allowed to use

The manuscript is used to generate the ground-truth knowledge artifact.
The starting data are used to build the participant package and must be safe to expose to future participant agents.

## What This Repo Generates

For each case, this repo should generate:

- `csag/`
  - the ground-truth knowledge artifact and validation report
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

## Case Layout

The intended forward-looking layout for one case is:

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

Notes:

- `data/` is the manuscript-to-ground-truth path.
- `starting_data/` is the participant-visible scientific starting point.
- `csag/` is internal ground truth.
- `prompt/` and `exports/participant/` are what future participant agents should receive.
- `scoring/` and `exports/evaluator/` are what the evaluator should receive.

Some older files in the repository still reflect an earlier benchmark or reproduction workflow. Treat them as legacy context unless you are explicitly migrating them.

## Authoring Workflow

For a new case:

1. stage the manuscript under `ground_truth/<case>/data/`
2. stage participant-visible starting data under `ground_truth/<case>/starting_data/`
3. run `paper-to-md`
   - for scientific papers with recoverable figures or tables, render relevant PDF pages to PNG and use them to populate `figure_interpretation`
4. run `csag-extraction`
5. generate the participant prompt package
6. generate the scoring schema package
7. export participant-facing and evaluator-facing subsets

The expected repo-local skill order is:

1. `paper-to-md`
2. `csag-extraction`
3. `benchmark-prompt-package`
4. `benchmark-scoring`

Active workflow helper scripts should live under the owning skill directory.
Do not treat a root-level `scripts/` directory as the default place for new workflow logic.
The `paper-to-md` skill should be self-contained under `skills/paper-to-md/`.

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
- the CSAG artifact and validation report exist
- the participant prompt package exists
- the scoring schema package exists
- the participant export excludes ground-truth-only assets
- the evaluator export includes the scoring assets needed for comparison
