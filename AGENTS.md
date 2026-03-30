# Repository Instructions

This repository stores benchmark cases. Your job is to add or refresh one case without changing the overall layout.

## Entry point

When a user wants to turn one paper, preprint, or project description into a benchmark case, expect the source file to be staged first at:

- `ground_truth/<case>/data/<case>.pdf`
- `ground_truth/<case>/data/<case>.docx`
- or `ground_truth/<case>/data/<case>.md`

Your first job is to treat `ground_truth/<case>/data/` as the source location. Do not search the whole repository for an arbitrary paper file if the case name is already known.

For a new staged source, generate these outputs in these exact locations:

- `papers/<case>.md`
- `ground_truth/<case>/data/<case>.md`
- `ground_truth/<case>/csag/raw_paper.json`
- `ground_truth/<case>/csag/csag_light.json`
- `ground_truth/<case>/<case>.prompt`
- `ground_truth/<case>/analysis/reference_analysis.md`

If the source arrives as PDF or DOCX, normalize it into `ground_truth/<case>/data/<case>.md` before generating the CSAG artifact.

When a human asks for a prompt they can paste into Codex or Claude Code, use this template:

```text
Use the repo-local AGENTS.md and the repo-local skills/csag-light skill.

Create a new benchmark case named <case> from the staged source document at:
ground_truth/<case>/data/<case>.<ext>

Generate these files:
- papers/<case>.md
- ground_truth/<case>/data/<case>.md
- ground_truth/<case>/csag/raw_paper.json
- ground_truth/<case>/csag/csag_light.json
- ground_truth/<case>/<case>.prompt
- ground_truth/<case>/analysis/reference_analysis.md

If possible, also scaffold reproduction notes under:
- ground_truth/<case>/reproduction/

Do not modify any existing benchmark cases.
```

## First principles

- Keep the case self-contained.
- Keep the prompt solver-facing and non-leaking.
- Keep the ground truth richer than the prompt.
- Be explicit about what is staged, what is held out, and what is only partially reproducible.
- Do not claim a full rerun when only a subset was reproduced.

## Read before acting

Before you make changes:

- Read `tasks/lessons.md` if it exists.
- Read the active section in `tasks/todo.md`.
- Inspect one or two existing cases under `ground_truth/` and match their style.
- Stop and ask if you cannot tell which assets are solver-visible versus held-out.

## Required case layout

For a new case named `<case>`:

```text
papers/<case>.md
ground_truth/<case>/<case>.prompt
ground_truth/<case>/data/
ground_truth/<case>/csag/
ground_truth/<case>/analysis/
ground_truth/<case>/reproduction/
```

Required files:

- `papers/<case>.md`
- `ground_truth/<case>/<case>.prompt`
- `ground_truth/<case>/data/<case>.md`
- `ground_truth/<case>/csag/raw_paper.json`
- `ground_truth/<case>/analysis/reference_analysis.md`

Recommended files:

- `ground_truth/<case>/data/<case>.pdf`
- `ground_truth/<case>/data/download_manifest.json`
- `ground_truth/<case>/csag/paper_extraction.json` or `.yaml`
- `ground_truth/<case>/csag/csag_light.json`
- `ground_truth/<case>/reproduction/pixi.toml`
- `ground_truth/<case>/reproduction/run.sh`
- `ground_truth/<case>/reproduction/summary.json`
- `ground_truth/<case>/reproduction/reproduction_report.md`

## Workflow

1. Choose the next case identifier.
2. Write `papers/<case>.md` with citation, field, public inputs, and a short reproducibility note.
3. Stage the source material under `ground_truth/<case>/data/`.
4. Create `ground_truth/<case>/data/<case>.md` as the canonical text source.
5. Build `ground_truth/<case>/csag/raw_paper.json` from the staged canonical text.
6. Run `csag-extraction` when a full CSAG record is warranted.
7. If a compact artifact is useful, project to `ground_truth/<case>/csag/csag_light.json` with the repo-local `skills/csag-light` flow.
8. Write `ground_truth/<case>/<case>.prompt`.
9. Write `ground_truth/<case>/analysis/reference_analysis.md`.
10. Add `ground_truth/<case>/reproduction/` and capture runtime or resource evidence when a rerun is feasible.
11. Verify that the prompt does not leak the held-out answer.

The generated prompt path is always `ground_truth/<case>/<case>.prompt`. It is part of the output package, not an input supplied by the contributor.

## CSAG rules

- Prefer `csag-extraction` first.
- Keep `csag/raw_paper.json` even if you also produce a lighter artifact.
- If you use `csag_light.json`, treat it as a projection of the canonical CSAG reasoning, not as a separate interpretation.
- The reference analysis must agree with the CSAG record on the main claims and evidence.

## Prompt rules

The prompt must contain:

- the working question
- the allowed starting datasets
- the held-out assets
- a short task statement

The prompt must not contain:

- the paper conclusions
- the reference analysis
- author figures or result summaries
- CSAG content
- solver-invisible file contents

## Reference analysis rules

`analysis/reference_analysis.md` should describe what strong work looks like.

Include:

- the scientific question in plain language
- the expected data handling and quality checks
- the methods, comparisons, or controls that matter
- the evidence needed to support the main claims
- common weak outputs that should score poorly
- the important outputs or figures a strong submission should produce

Do not turn this file into a hidden solution notebook. It should guide evaluation, not replace it.

## Runtime and resource rules

When reruns are possible:

- capture commands, elapsed time, and peak memory
- write `summary.json` and `reproduction_report.md`
- store raw logs under `logs/` and structured step metrics under `metrics/`
- label the case `complete`, `partial`, or `blocked` truthfully

When reruns are not possible:

- still create `reproduction/reproduction_report.md`
- explain the blocker clearly
- record any partial reruns and what they do prove

If `.claude_resources.json` exists, use it as the host profile. Set and record an explicit benchmark cap for the case rather than silently using the full machine.

## Document conversion

There is no dedicated general PDF or DOCX to Markdown conversion skill in the current workspace.

Use the best available path:

- If the source is on AlphaXiv or arXiv, `implement-paper-auto` is the closest fit.
- For DOCX, prefer a local converter such as `pandoc`.
- For PDF, use a local extractor and clean the result into `data/<case>.md`.

Do not skip the Markdown source just because conversion is imperfect. Clean it until it is usable for CSAG extraction.

## Verification

Before you stop, confirm all of the following:

- The case tree exists in the standard location.
- The prompt names only solver-visible inputs.
- The held-out paper or project text is present under `data/`.
- `analysis/reference_analysis.md` exists.
- `csag/` contains a usable structured artifact.
- `reproduction/` contains either runtime evidence or an honest limitation report.
- `tasks/todo.md` reflects the work you completed.
