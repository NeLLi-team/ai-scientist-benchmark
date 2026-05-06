---
name: csag-extraction
description: >-
  Extract a CSAG (Conditional Scientific Argumentation Graph) from scientific papers using a canonical,
  scalable argumentation spine (Assertions, EvidenceItems, EvidenceLinks, InferenceSteps) while enforcing
  CSAG conditionality (no assertion without at least one Context). Also generates paper-grounded Q&A items
  using bundled QA templates.
license: CC0-1.0
metadata:
  version: "0.3.0"
---

# CSAG extraction skill

## Goal

Convert a paper into a **CSAG `PaperExtraction`** instance that is:

- **Schema-valid** (LinkML: `assets/csag.yaml`)
- **Evidence-grounded** (TextSpans for key objects)
- **Canonical** (support/refute only via `EvidenceLink`, chains via `InferenceStep`)
- **Conditional** (every Assertion has ≥1 Context)

## Files in this skill

- `assets/csag.yaml` — authoritative schema
- `assets/csag_qa_templates.yaml` — QA template catalog
- `references/CSAG_PLAYBOOK.md` — detailed extraction guide + edge cases

## Non‑negotiable invariants

1) **Every Assertion MUST have ≥1 Context** (schema-enforced).
2) **Support/refute polarity ONLY in `EvidenceLink`**.
3) **Contradictions/qualification ONLY in `AssertionRelation`**.
4) **Reasoning chains ONLY as `InferenceStep`**.
5) Ground important objects to TextSpans.
6) Every Assertion MUST have `normalization_status`:
   - `raw` / `partially_normalized` / `fully_normalized`

## Procedure (phased extraction)

## Retrieval vs extraction scope (critical)

When used with literature search (e.g., `polars-dovmed`):

1. Use discovery terms to find candidate papers.
2. Then perform CSAG extraction on the **full paper content**.

Important:
- Discovery/search terms are for paper selection only.
- Do **not** restrict extracted assertions/evidence to sentences that contain the search terms.
- If the paper is about a broader mechanism (e.g., actin genes in giant viruses), extract that broader reasoning structure even when trigger terms appear only in taxonomy/background sentences.

### Two-stage literature workflow (required with `polars-dovmed`)

1. **Discovery stage (retrieval only)**
   - Use topic/entity terms only to identify candidate papers.
   - Deduplicate candidates by PMID/PMCID before extraction.
   - Retrieve full text for selected papers (`include_full_text=true`).
2. **Extraction stage (analysis)**
   - Extract CSAG objects from the entire paper (title/abstract/introduction/methods/results/discussion/conclusion).
   - Mention patterns can guide navigation, but must not define extraction boundaries.

### Phase 1 — Core graph (always)
1. Build `PaperExtraction` metadata (id/title/doi/pmid if available).
   - Resolve `doi` and `pmid` from the staged source, OCR/article outputs, TEI/XML, or local metadata whenever recoverable.
   - If one or both cannot be resolved, record explicit `doi_status` / `pmid_status` entries in `extraction_activities.parameters` with `resolved` or `unresolved`.
2. Extract **Artifacts** when the paper exposes figure/table/supplement captions.
3. Extract **Datasets** when the paper exposes data-availability text, repository links, accessions, or project identifiers.
4. Extract **Entities** with ontology annotations when possible.
5. Extract **Assertions** (hypotheses, result-claims, conclusions):
   - must include `contexts` (≥1)
   - must include `normalization_status`
   - for benchmark ground truth, include `criticality` (`core`, `major`, `supporting`, or `background`)
   - for benchmark ground truth, include `falsification_criteria` describing what would weaken or refute the assertion
6. Extract **EvidenceItems** (results/analyses/citations).
7. Create **EvidenceLinks** (EvidenceItem -> Assertion) with `polarity`, `strength`, `rationale`.
8. Add **TextSpans** grounding key Assertions/EvidenceItems/EvidenceLinks.

### Phase 2 — Study & experiment structure (if feasible)
- Add `Study` and `Experiment` objects.
- Enrich Contexts (organism/cell_type/tissue/disease) using Entity references.

### Phase 3 — Conditions + reasoning + critique/gaps + QA (when available)
- Add `Condition` objects (dose/time/genotype/treatment regime).
- Add `InferenceStep`s for explicit/implicit reasoning chains.
- Add `StudyCritique` and `KnowledgeGap` objects.
- Generate QA items from templates.

## Minimum coverage expectations (full research articles)

Unless the source is a short note/editorial with genuinely limited content, target:
- >=1 assertion for hypothesis/research-question/objective when present
- >=2 result/conclusion assertions from different parts of the paper when present
- >=2 evidence items and >=2 evidence links when present
- >=1 inference step when reasoning combines multiple premises/evidence
- >=1 critique/gap when explicitly discussed by authors
- >=1 artifact when figure/table captions are present in the source
- >=1 dataset when data-availability text, accessions, or repository links are present in the source

If a category is not present in the paper, state that in `notes` for the paper/assertion rather than silently omitting it.

## Extraction quality gate (before finalizing a paper)

Confirm all checks:
- Assertions are not just keyword-hit snippets; they reflect core study claims.
- Evidence links cover key claims, not only the first matched sentence.
- At least one TextSpan anchors each non-trivial extracted object.
- Output includes explicit statement of absent components (e.g., no clear hypothesis section).
- `doi` / `pmid` are resolved when recoverable, or explicit `doi_status` / `pmid_status` parameters are present in `extraction_activities`.
- `artifacts` are present when figure/table captions are present in the source.
- `datasets` are present when data-availability text, accessions, or repository links are present in the source.
- The repo-local validation pass succeeds.
- For benchmark ground truth, run validation with `--profile ground_truth` and resolve all strict assertion/evidence checks.

## Anti-patterns (forbidden)

- Do not emit a paper with only one assertion/evidence pair when richer claims are present.
- Do not treat search keyword mentions as a proxy for the paper's core findings.
- Do not skip mechanistic/statistical claims only because they lack discovery keywords.

## PubMed provenance requirements (mandatory)

1. Track PMID for every extracted paper whenever available.
2. Populate `PaperExtraction.pmid` when PMID is available from retrieval metadata.
3. Anchor `TextSpan.document_id` to `pmid:<ID>` when PMID is known.
4. If only PMCID is available, use `pmc:<ID>` and mark PMID resolution as pending in `notes`.
5. Do not mix spans from different papers in one `PaperExtraction`; produce one extraction per source paper.

## Repo-local validation hook (mandatory)

Before finalizing the extraction, run:

```bash
python skills/csag-extraction/scripts/validate_paper_extraction.py \
  /abs/path/to/paper_extraction.json \
  --profile ground_truth \
  --source-markdown /abs/path/to/paper.md \
  --article-json /abs/path/to/paper.article.json \
  --report-out /abs/path/to/paper_extraction.validation.json
```

This validation step is blocking:

- do not stop after writing `paper_extraction.json`
- do not stop after informal inspection only
- do not stop if the validator reports missing DOI/PMID status, missing artifacts, missing datasets, or broken references

## Mandatory paper interrogation questions

Before finalizing a `PaperExtraction`, answer these (internally or as `qa_items`):
- What are the paper's hypotheses/research questions/objectives?
- What are the primary result claims and conclusions/discoveries?
- What evidence supports each key claim, and what evidence (if any) refutes it?
- Which assertions are core, major, supporting, or background for benchmark evaluation?
- What observation or analysis would falsify or seriously weaken each core/major assertion?
- What inference/mechanistic chains connect evidence to conclusions?
- What limitations/flaws are stated or strongly implied?
- What open knowledge gaps/future-work items are stated?

Map answers to CSAG objects:
- hypotheses/results/conclusions -> `assertions`
- benchmark importance -> assertion `criticality`
- falsifiability checks -> assertion `falsification_criteria`
- support/refute -> `evidence_links`
- reasoning chains -> `inferences`
- limitations -> `critiques`
- open questions -> `knowledge_gaps`
- question-driven outputs -> `qa_items` (from `assets/csag_qa_templates.yaml`)

## Normalization rubric

- `raw`: free-text only; triple fields may be empty.
- `partially_normalized`: some of {subject,predicate,object} filled OR ambiguous mappings.
- `fully_normalized`: subject+predicate+object present; refer to Entity IDs; predicate is CURIE/URI (RO/SIO preferred).

## QA templates

Use `assets/csag_qa_templates.yaml` to instantiate `QAItem` + `Answer` objects.
Answers must cite `supporting_assertions` and/or `supporting_evidence_links`.

For edge cases and scoring guidance, see `references/CSAG_PLAYBOOK.md`.
