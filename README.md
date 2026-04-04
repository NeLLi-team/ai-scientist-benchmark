# Advanced Benchmark

This repository is currently used for **knowledge graph generation from manuscript or publication source files**, especially PDFs.

The core workflow is:

1. stage a source file under `ground_truth/<case>/data/`
2. convert it to canonical Markdown
3. extract a CSAG knowledge graph from that text

New work in this repository should focus on **PDF/DOCX/Markdown -> Markdown -> CSAG**.

## Quick start

To add one new manuscript or publication:

1. choose a case name such as `06-my-paper`
2. create:
   - `ground_truth/06-my-paper/data/`
3. place the source file there as one of:
   - `ground_truth/06-my-paper/data/06-my-paper.pdf`
   - `ground_truth/06-my-paper/data/06-my-paper.docx`
   - `ground_truth/06-my-paper/data/06-my-paper.md`
4. run an agent from the repo root with instructions equivalent to:

```text
Use the repo-local AGENTS.md.

The source document is staged at:
ground_truth/06-my-paper/data/06-my-paper.pdf

Use the repo-local `paper-to-md` skill to create:
- ground_truth/06-my-paper/data/06-my-paper.md

Then use the repo-local `csag-extraction` skill to create:
- ground_truth/06-my-paper/csag/raw_paper.json
- ground_truth/06-my-paper/csag/paper_extraction.json

Do not create prompts, reproduction artifacts, or benchmark evaluation material unless explicitly asked.
```

## Repository layout

The main working layout is:

```text
ground_truth/
  <case>/
    data/
      <case>.pdf | <case>.docx | <case>.md
      <case>.md
    csag/
      raw_paper.json
      paper_extraction.json
```

Notes:

- the original source stays under `data/`
- the canonical Markdown also lives under `data/`
- the CSAG knowledge graph lives under `csag/`

## What this repo does

For new work, this repo does:

- source staging under `ground_truth/<case>/data/`
- PDF or DOCX normalization to Markdown
- CSAG knowledge graph extraction from the canonical text

For new work, this repo does **not** require:

- reproduction of published results
- runtime benchmarking
- prompt packaging
- evaluation reference writeups

Some older files in the repository may still reflect an earlier benchmark-oriented workflow. Keep them intact unless you are explicitly asked to migrate them.

## Skills used here

The expected skill order is:

1. `paper-to-md`
2. `csag-extraction`

Use `csag-light` only when a compact projection is explicitly requested in addition to the full graph.

## Acceptance checklist

A new knowledge-graph case is ready when:

- the source file exists under `ground_truth/<case>/data/`
- `ground_truth/<case>/data/<case>.md` exists
- `ground_truth/<case>/csag/raw_paper.json` exists
- `ground_truth/<case>/csag/` contains a full CSAG extraction

That is the default scope for this repository right now.
