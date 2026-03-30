# Advanced Benchmark

This repository stores benchmark cases for research workflows run with tools such as Codex or Claude Code. Each case turns one recent paper or one project description into a package with a research question, solver-visible starting data, held-out ground truth, a structured reasoning record, and reference materials for evaluation.

The goal is simple: a solver should work only from the prompt and the allowed data. Its output can then be compared against the held-out paper or project materials, the CSAG record, the reference analysis, and the runtime evidence.

## Quick start for contributors

If you are contributing one new paper or one new project description, use this exact staging pattern.

1. Pick a case name, for example `06-my-project`.
2. Create `ground_truth/06-my-project/data/`.
3. Copy your source document into that directory and rename it to one of:
   - `ground_truth/06-my-project/data/06-my-project.pdf`
   - `ground_truth/06-my-project/data/06-my-project.docx`
   - `ground_truth/06-my-project/data/06-my-project.md`
4. Launch Codex or Claude Code from the repository root.
5. Give it this prompt:

```text
Use the repo-local AGENTS.md and the repo-local skills/csag-light skill.

Create a new benchmark case named 06-my-project from the staged source document at:
ground_truth/06-my-project/data/06-my-project.pdf

Generate these files:
- papers/06-my-project.md
- ground_truth/06-my-project/data/06-my-project.md
- ground_truth/06-my-project/csag/raw_paper.json
- ground_truth/06-my-project/csag/csag_light.json
- ground_truth/06-my-project/06-my-project.prompt
- ground_truth/06-my-project/analysis/reference_analysis.md

If possible, also scaffold reproduction notes under:
- ground_truth/06-my-project/reproduction/

Do not modify any existing benchmark cases.
```

For a DOCX source, change only the staged path in the prompt. The agent should normalize the source into `ground_truth/<case>/data/<case>.md` before generating the CSAG artifact.

The key generated paths are:

- source file goes in `ground_truth/<case>/data/`
- normalized Markdown goes in `ground_truth/<case>/data/<case>.md`
- generated solver prompt goes in `ground_truth/<case>/<case>.prompt`
- CSAG-light knowledge artifact goes in `ground_truth/<case>/csag/csag_light.json`
- reference analysis goes in `ground_truth/<case>/analysis/reference_analysis.md`

## What a benchmark case contains

Each case should contain:

- A short registry entry in `papers/`.
- The original source material in `ground_truth/<case>/data/`.
- A normalized Markdown version of the paper or project description.
- A case prompt with the research question and the allowed starting data only.
- A CSAG-based reasoning record in `ground_truth/<case>/csag/`.
- A reference analysis that shows what strong output should cover.
- A reproduction package with scripts, logs, and runtime or resource measurements when that is feasible.

New cases should include both `data/<case>.pdf` and `data/<case>.md` when the source is a paper. Older cases in this repository may instead contain XML or HTML extracts; keep them intact, but use Markdown for new additions.

If the source is a project description rather than a paper, store the original source file under `data/` and still create a cleaned canonical `data/<case>.md`.

The `analysis/` directory described below is the standard for new cases. Some older cases in this repository still concentrate that material in `reproduction/reproduction_report.md`.

## Repository layout

```text
.
├── papers/
│   └── 06-example-study.md
├── ground_truth/
│   └── 06-example-study/
│       ├── 06-example-study.prompt
│       ├── data/
│       │   ├── 06-example-study.pdf
│       │   ├── 06-example-study.md
│       │   ├── download_manifest.json
│       │   └── ...
│       ├── csag/
│       │   ├── raw_paper.json
│       │   ├── paper_extraction.json
│       │   └── csag_light.json
│       ├── analysis/
│       │   └── reference_analysis.md
│       └── reproduction/
│           ├── pixi.toml
│           ├── run.sh
│           ├── summary.json
│           ├── reproduction_report.md
│           ├── token_budget.json
│           ├── metrics/
│           └── logs/
└── tasks/
```

Required files for a new case:

- `papers/<case>.md`: one-paragraph registry note with citation, field, available public inputs, and an honest reproducibility note.
- `ground_truth/<case>/<case>.prompt`: research question, allowed starting datasets, held-out assets, and task instructions.
- `ground_truth/<case>/data/<case>.md`: cleaned canonical text used to derive the CSAG and the reference analysis.
- `ground_truth/<case>/csag/raw_paper.json`: staged full-text record used as the extraction source.
- `ground_truth/<case>/analysis/reference_analysis.md`: concise reference analysis for evaluation.

Recommended additional files:

- `ground_truth/<case>/data/download_manifest.json`: source URLs, checksums, and staging notes.
- `ground_truth/<case>/csag/paper_extraction.json` or `.yaml`: full CSAG output when available.
- `ground_truth/<case>/csag/csag_light.json`: compact projection for downstream comparison.
- `ground_truth/<case>/reproduction/summary.json`: step-level runtime and memory summary.
- `ground_truth/<case>/reproduction/reproduction_report.md`: plain-language report on what was reproduced, what was not, and why.

## Contributor workflow

1. Choose a recent paper or project description that has enough public or shareable material to stage locally.
2. Create a new case identifier with a zero-padded numeric prefix and a short slug, for example `06-example-study`.
3. Add `papers/<case>.md` with the citation, field, public inputs, and a short reproducibility note.
4. Create `ground_truth/<case>/data/` and stage the source material, public starting datasets, and a download manifest.
5. Create `ground_truth/<case>/data/<case>.md` as the canonical text version of the paper or project description.
6. Write `ground_truth/<case>/<case>.prompt` so it contains only the research question, allowed starting datasets, held-out assets, and task.
7. Generate the CSAG record under `ground_truth/<case>/csag/`.
8. Write `ground_truth/<case>/analysis/reference_analysis.md` to describe the expected scope, methods, checks, and outputs of a strong submission.
9. If a rerun is feasible, add `ground_truth/<case>/reproduction/` with scripts, environment, logs, and runtime or resource measurements. If a full rerun is not feasible, still write an honest `reproduction_report.md` that explains the limit and records any partial evidence.
10. Verify that the prompt does not leak held-out assets or author outputs.

## CSAG workflow

Use the full CSAG extraction first when possible, then project to the lighter case artifact if needed.

- Preferred extraction path: `csag-extraction`
- Compact projection path: repo-local `skills/csag-light`

At minimum, a new case should keep:

- `csag/raw_paper.json`
- one structured CSAG output

The reference analysis and the evaluation workflow should agree with the CSAG record. If the CSAG and the analysis disagree, the case is not ready.

## Prompt design rules

The prompt is the solver-facing problem statement. Keep it narrow and clean.

- Include the working question.
- List the allowed starting datasets explicitly.
- List the held-out assets explicitly.
- Tell the solver not to use the paper text, author code, figures, or `csag/` unless those assets are intentionally solver-visible.
- Do not include conclusions, reported metrics, or the answer key.

The current prompt files in `ground_truth/*/*.prompt` are the style reference.

## Reference analysis

`analysis/reference_analysis.md` is the evaluation anchor for a strong answer. It should explain:

- the core scientific question
- the minimum dataset handling expected
- the methods or checks a strong submission should perform
- the claims that must be supported by evidence
- important failure modes or shortcuts that should lose credit
- what is intentionally out of scope

This file should read like a sober reviewer note, not like a solution dump.

## Runtime and resource evidence

When the repository can support a rerun, capture runtime and resource usage under `reproduction/`.

- `summary.json` should record step count, elapsed time, peak memory, failed steps, and per-step commands.
- `reproduction_report.md` should summarize status as `complete`, `partial`, or `blocked`.
- `token_budget.json` can record text-budget estimates when exact tool token counts are unavailable.
- `logs/` and `metrics/` should keep the raw evidence behind the summary.

When a full rerun is not possible, document the blocker honestly and keep any partial reruns clearly labeled.

## Document conversion

The current skill set has strong CSAG support but does not include a dedicated general PDF or DOCX to Markdown conversion skill.

Practical options in this workspace:

- `implement-paper-auto` can help when the source is already available through AlphaXiv or arXiv.
- DOCX can usually be normalized locally with `pandoc`.
- PDF usually needs a local extractor plus manual cleanup before it becomes a reliable `data/<case>.md`.

This is a tooling limitation, not a reason to skip the Markdown source. New cases should still include a cleaned Markdown file.

## Acceptance checklist

A new case is ready when all of the following are true:

- `papers/<case>.md` exists.
- `ground_truth/<case>/data/<case>.md` exists.
- `ground_truth/<case>/<case>.prompt` exists and does not leak the answer.
- `ground_truth/<case>/csag/` contains a usable structured reasoning artifact.
- `ground_truth/<case>/analysis/reference_analysis.md` exists.
- `ground_truth/<case>/reproduction/` either contains tracked runtime evidence or an honest report explaining why that evidence is partial or unavailable.
