# Repository Instructions

This repository is for **knowledge graph generation from manuscript or publication PDFs**.

The default workflow is:

1. stage a source document under `ground_truth/<case>/data/`
2. convert it to canonical Markdown with the repo-local `paper-to-md` skill
3. extract a CSAG knowledge graph with the repo-local `csag-extraction` skill

Do not expand the scope beyond that unless the user explicitly asks.

## Entry point

If the user provides a manuscript, paper, preprint, or publication PDF, put it here:

- `ground_truth/<case>/data/<case>.pdf`

If the source is DOCX or already Markdown, use:

- `ground_truth/<case>/data/<case>.docx`
- `ground_truth/<case>/data/<case>.md`

`<case>` should be a project-specific directory name under `ground_truth/`, for example:

- `ground_truth/06-my-paper/data/06-my-paper.pdf`

When the case name is already known, treat `ground_truth/<case>/data/` as the only source location. Do not scan the whole repository for the input file.

## Read before acting

Before making changes:

- read `tasks/lessons.md` if it exists
- read the active section in `tasks/todo.md`
- inspect one existing `ground_truth/<case>/` tree to match the repo style

## Required workflow

For a staged PDF or DOCX source:

1. keep the original source file under `ground_truth/<case>/data/`
2. use the repo-local `paper-to-md` skill to create:
   - `ground_truth/<case>/data/<case>.md`
3. create `ground_truth/<case>/csag/`
4. create:
   - `ground_truth/<case>/csag/raw_paper.json`
5. use the repo-local `csag-extraction` skill to generate at least one full CSAG export, preferably:
   - `ground_truth/<case>/csag/paper_extraction.json`

The canonical Markdown at `ground_truth/<case>/data/<case>.md` is the bridge between PDF conversion and graph extraction.

## Output contract

For a new case named `<case>`, the minimum expected files are:

```text
ground_truth/<case>/
  data/
    <case>.pdf | <case>.docx | <case>.md
    <case>.md
  csag/
    raw_paper.json
    paper_extraction.json
```

If the source arrives as Markdown already, still keep it under `data/` and use that as the canonical source for CSAG extraction.

## Skills to use

Use these repo-local skills in this order:

1. `paper-to-md`
2. `csag-extraction`

Use `csag-light` only if the user explicitly asks for a compact projection in addition to the full CSAG extraction.

## Scope limits

Unless the user explicitly asks otherwise, do **not** create or update:

- `papers/<case>.md`
- `<case>.prompt`
- `analysis/reference_analysis.md`
- `reproduction/`

This repo may still contain older benchmark and reproduction material. Treat that as legacy context, not the default workflow for new work.

## Document conversion rule

If the input is a PDF:

- use the repo-local `paper-to-md` skill
- keep the canonical Markdown in `ground_truth/<case>/data/<case>.md`

If the input is a DOCX:

- place it in `ground_truth/<case>/data/`
- still normalize to `ground_truth/<case>/data/<case>.md`

Do not stop at OCR or temporary markdown. The task is only complete when the canonical Markdown exists and the CSAG extraction has been produced.

## CSAG rule

Use `csag-extraction` to produce a full knowledge graph grounded in the paper text.

At minimum:

- keep `raw_paper.json`
- keep one full CSAG output file

The extracted graph should be derived from the canonical Markdown or the staged text source, not from memory.

## Verification

Before stopping, confirm:

- the source PDF or DOCX is stored under `ground_truth/<case>/data/`
- `ground_truth/<case>/data/<case>.md` exists
- `ground_truth/<case>/csag/raw_paper.json` exists
- `ground_truth/<case>/csag/` contains a full CSAG extraction
- `tasks/todo.md` reflects the work you completed
