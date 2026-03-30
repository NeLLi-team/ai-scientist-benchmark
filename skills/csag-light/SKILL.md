---
name: csag-light
description: >-
  Produce a compact CSAG-derived scientific logic export for training and analytics by projecting
  full-paper reasoning into a lightweight JSON record with paper_id, title, entities, hypotheses,
  evidence_items, evidence_links, discoveries, gaps, conclusions, and confidence. Use when the
  user wants CSAG-style evidence grounding in a smaller LM-friendly format rather than full
  LinkML `PaperExtraction`.
license: CC0-1.0
metadata:
  version: "0.2.0"
---

# CSAG-light

## Goal

Produce a compact, auditable scientific logic record that is **derived from CSAG**, not a competing schema.

Prefer this skill when the user wants:

- SFT / preference-training data
- compact JSON exports
- easier downstream analytics
- a lighter view over literature logic than full `PaperExtraction`

## Files in this skill

- `references/CSAG_LIGHT_PLAYBOOK.md` — compact extraction/projection workflow
- `scripts/validate_csag_light.py` — deterministic validator for artifacts and run directories
- `scripts/repair_csag_light.py` — deterministic repair loop for rough artifacts
- `../csag-extraction/assets/csag.yaml` — authoritative canonical schema
- `../csag-extraction/references/CSAG_PLAYBOOK.md` — full CSAG extraction policy

## Core rule

**CSAG is canonical. `csag-light` is a projection.**

Preferred workflow:

1. retrieve the paper
2. extract canonical CSAG reasoning
3. project that reasoning into `csag-light`

Direct extraction into `csag-light` is allowed only when the task is explicitly lightweight or speed-sensitive. Even then, reason internally in CSAG terms first: assertions, evidence items, evidence links, contexts, gaps, critiques, and inference steps.

## Non-negotiable invariants

1. Use full-paper scope, not keyword-hit snippets.
2. Keep support/refute/limits semantics in `evidence_links`, not in free text.
3. Every non-trivial record must be provenance-grounded.
4. `discoveries` and `conclusions` must be backed by evidence or mapped assertions.
5. `confidence` must reflect support quality, not writing style.
6. When information is lost in projection, prefer explicit notes/flags over silent deletion.
7. Provenance quotes must be exact substrings of the staged source text.
8. Every `evidence_link` must have non-empty provenance.
9. Use canonical compact IDs and controlled vocabularies from the playbook.

## When to use `csag-light`

- The user wants a small JSON record instead of LinkML YAML.
- The output is meant for LLM training, evaluation, ranking, or retrieval.
- The study is simple enough that flattening conditions and contexts is acceptable.

## Benchmark repository convention

When this skill is used inside this benchmark repository, assume the source document has already been staged at:

- `ground_truth/<case>/data/<case>.pdf`
- `ground_truth/<case>/data/<case>.docx`
- or `ground_truth/<case>/data/<case>.md`

Expected minimum outputs for a new benchmark case:

- `ground_truth/<case>/data/<case>.md` as the cleaned canonical text
- `ground_truth/<case>/csag/raw_paper.json` as the staged extraction source
- `ground_truth/<case>/csag/csag_light.json` as the compact knowledge artifact

Common companion outputs in this repository:

- `ground_truth/<case>/<case>.prompt`
- `ground_truth/<case>/analysis/reference_analysis.md`
- `papers/<case>.md`

If the source starts as PDF or DOCX, normalize it into `data/<case>.md` first. Then derive `raw_paper.json`, then write `csag_light.json`.

## When not to use `csag-light` alone

- The task needs condition-aware truth or detailed study structure.
- The paper has important multi-step mechanistic reasoning.
- The task depends on rich normalization, ontology mappings, or assertion-to-assertion relations.
- The output must be schema-valid CSAG.

In those cases, use `csag-extraction` first and keep the full artifact.

## Required output shape

Return one JSON object per paper with these top-level fields:

```json
{
  "paper_id": "...",
  "title": "...",
  "entities": [...],
  "hypotheses": [...],
  "evidence_items": [...],
  "evidence_links": [...],
  "discoveries": [...],
  "gaps": [...],
  "conclusions": [...],
  "confidence": {
    "document_confidence": 0.0,
    "rationale": "...",
    "flags": [...]
  }
}
```

Before finalizing an artifact, validate it with:

```bash
python skills/csag-light/scripts/validate_csag_light.py \
  --artifact /path/to/artifact.json \
  --raw-paper /path/to/raw_paper.json
```

If a rough artifact fails because of ID drift, missing link provenance, or approximate quotes, run the repair loop before discarding it:

```bash
python skills/csag-light/scripts/repair_csag_light.py \
  --artifact /path/to/rough_artifact.json \
  --raw-paper /path/to/raw_paper.json \
  --output /path/to/repaired_artifact.json
```

For field definitions, mapping rules, canonical IDs, exact-quote requirements, and quality gates, read `references/CSAG_LIGHT_PLAYBOOK.md`.
