# CSAG-light Playbook

`csag-light` is a compact export format for scientific reasoning. It is intentionally smaller and easier to use for model training than full CSAG, but it is also intentionally lossy.

## Canonical stance

Treat full CSAG as the source of truth.

Use this order whenever feasible:

1. retrieve full paper content
2. extract canonical CSAG objects
3. project into `csag-light`

If you extract directly into `csag-light`, still reason as if you were building:

- `Assertion`
- `EvidenceItem`
- `EvidenceLink`
- `Context`
- `InferenceStep`
- `StudyCritique`
- `KnowledgeGap`

Then flatten carefully.

## Output contract

Return one JSON object per paper:

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

Validation rule:

- an artifact is not complete until it passes `scripts/validate_csag_light.py`
- if a rough artifact is close but fails on formatting or provenance exactness, run `scripts/repair_csag_light.py` and validate again

## What `csag-light` keeps

- paper identity
- core entities
- hypothesis-like claims
- atomic evidence units
- evidence-to-claim edges
- main discoveries
- knowledge gaps and major limitations
- broad conclusions
- extraction confidence

## What `csag-light` compresses or loses

- explicit `Context` objects
- explicit `Condition` objects
- detailed study and experiment structure
- `AssertionRelation`
- explicit `InferenceStep` chains
- full ontology normalization details
- rich QA objects

If these details matter, keep full CSAG alongside the compact export.

## Canonical compact conventions

### Provenance format

Use only:

```json
{
  "section": "abstract|full_text",
  "quote": "..."
}
```

Rules:

- `quote` must be an exact substring from the staged raw text
- do not use ellipses
- do not normalize punctuation
- do not paraphrase
- keep quotes short and specific, usually `20-240` characters

### ID format

Use stable within-document IDs with these prefixes:

- entities: `ent01`, `ent02`, ...
- hypotheses: `hyp01`, `hyp02`, ...
- evidence items: `ev01`, `ev02`, ...
- evidence links: `link01`, `link02`, ...
- discoveries: `disc01`, `disc02`, ...
- gaps: `gap01`, `gap02`, ...
- conclusions: `conc01`, `conc02`, ...

Do not mix styles like `E01`, `e1`, and namespaced IDs in the same run.

### Controlled vocabularies

Allowed `entities[].type` values:

- `taxon`
- `gene`
- `protein`
- `pathway`
- `environment`
- `host`
- `phenotype`
- `assay`
- `method`
- `chemical`
- `dataset`
- `virus`
- `contig`
- `mag`
- `plasmid`
- `phage`
- `sampling_location`
- `concept`
- `process`
- `other`

Allowed `evidence_items[].type` values:

- `expression`
- `phylogeny`
- `homology`
- `comparative_genomics`
- `metagenomics`
- `ecological_observation`
- `biochemical_assay`
- `infection_assay`
- `imaging`
- `computational_prediction`
- `structure_prediction`
- `literature_prior`
- `other`

## Projection rules

### `paper_id`

Use this priority order:

1. DOI
2. PMID
3. PMCID
4. deterministic internal ID

### `title`

Use the exact title with whitespace normalization only.

### `entities`

Project CSAG `entities` into compact records:

```json
{
  "entity_id": "...",
  "name": "...",
  "type": "...",
  "aliases": [...],
  "canonical_form": "...",
  "grounding": {
    "database": "...",
    "accession": "..."
  },
  "provenance": [...],
  "confidence_score": 0.0
}
```

Rules:

- Preserve stable IDs when they exist.
- Map CSAG `entity_category` into the controlled compact `type` list above.
- Keep only the most relevant ontology grounding if many exist.
- Preserve provenance for important entities.
- Use `entNN` IDs.

### `hypotheses`

Map from CSAG `assertions` where `claim_role` is one of:

- `hypothesis`
- `research_question`
- `objective`

Compact shape:

```json
{
  "hypothesis_id": "...",
  "text": "...",
  "status": "proposed|supported|refuted|mixed",
  "entities": [...],
  "provenance": [...],
  "confidence_score": 0.0
}
```

Status mapping:

- `proposed` when the paper frames the claim without strong support
- `supported` when evidence links are mostly supportive
- `refuted` when evidence links are mostly refuting
- `mixed` when support is conflicting or limited

Guardrails:

- emit `1-4` hypotheses for a normal research article
- do not emit background facts as hypotheses
- use `hypNN` IDs

### `evidence_items`

Project CSAG `evidence_items` as atomic observations or analyses.

Compact shape:

```json
{
  "evidence_id": "...",
  "type": "...",
  "text": "...",
  "strength": "weak|moderate|strong",
  "entities": [...],
  "provenance": [...],
  "confidence_score": 0.0
}
```

Rules:

- Keep items smaller than conclusions.
- Do not encode support/refute polarity here.
- If the source is a figure/table result, preserve that in provenance.
- emit `2-10` evidence items for a normal article
- use `evNN` IDs

### `evidence_links`

This is the core field. Project CSAG `evidence_links` directly.

Compact shape:

```json
{
  "link_id": "...",
  "source_evidence_id": "...",
  "target_id": "...",
  "target_type": "hypothesis|discovery|conclusion|gap",
  "relation": "supports|contradicts|partially_supports|motivates|limits",
  "rationale": "...",
  "provenance": [...],
  "confidence_score": 0.0
}
```

Rules:

- Keep all support/refute semantics here.
- `relation=limits` is allowed when evidence reveals an important limitation.
- Do not create links without a real target record.
- every `evidence_link` must have non-empty provenance
- use the exact supporting quote from the linked evidence item or directly supporting sentence
- use `linkNN` IDs

### `discoveries`

Map from CSAG `assertions` where `claim_role=discovery`.

Compact shape:

```json
{
  "discovery_id": "...",
  "text": "...",
  "novelty": "new|confirmatory|incremental",
  "entities": [...],
  "supporting_evidence_ids": [...],
  "provenance": [...],
  "confidence_score": 0.0
}
```

Guardrails:

- emit `1-4` discoveries for a normal article
- use `discNN` IDs

### `gaps`

Primary source:

- CSAG `knowledge_gaps`

Secondary source when useful:

- CSAG `critiques` projected into gap form when the compact export needs to retain a major limitation

Compact shape:

```json
{
  "gap_id": "...",
  "text": "...",
  "type": "mechanistic|causal|sampling|annotation|experimental|generalization|critique",
  "related_ids": [...],
  "provenance": [...],
  "confidence_score": 0.0
}
```

Rule:

- If a projected gap comes from `StudyCritique`, mark that clearly in text or provenance.
- emit `1-4` gaps for a normal article
- do not pad with generic future-work filler
- use `gapNN` IDs

### `conclusions`

Map from CSAG `assertions` where `claim_role=conclusion`.

Compact shape:

```json
{
  "conclusion_id": "...",
  "text": "...",
  "scope": "specific|broad",
  "supported_by": [...],
  "limited_by": [...],
  "provenance": [...],
  "confidence_score": 0.0
}
```

Rules:

- Keep conclusions broader than discoveries.
- Do not extract vague generic takeaways that are not actually supported in the paper.
- emit `1-3` conclusions for a normal article
- use `concNN` IDs

### `confidence`

Use:

```json
{
  "document_confidence": 0.0,
  "rationale": "...",
  "flags": [...]
}
```

Compute it from:

- coverage of key claims
- quality of provenance
- number and diversity of supporting evidence items
- ambiguity of entity mapping
- contradictions or unresolved limits
- degree of compression loss from full CSAG

Never assign confidence based on polished prose alone.

## Recommended workflow

### 1. Retrieval

If discovery is needed, use `polars-dovmed`.

Rules:

- search terms are for retrieval only
- deduplicate by PMID/PMCID
- extract from full paper scope

### 2. Canonical extraction

Prefer `csag-extraction` first.

At minimum, think in terms of:

- entities
- assertions
- evidence items
- evidence links
- contexts
- gaps / critiques

### 3. Projection

Flatten from CSAG into the compact schema.

Mapping summary:

- `assertions` -> `hypotheses`, `discoveries`, `conclusions`
- `evidence_items` -> `evidence_items`
- `evidence_links` -> `evidence_links`
- `knowledge_gaps` -> `gaps`
- `critiques` -> optional projected `gaps`
- `entities` -> `entities`
- object-level `confidence_score` -> compact item confidence

### 4. Validation

Before finalizing, check:

- every discovery has supporting evidence or explicit evidence links
- every conclusion is supported or explicitly limited
- no `evidence_link` points to a missing target
- no major claim lacks provenance
- `gaps` are not just paraphrased conclusions
- unsupported conclusions are flagged, not hidden

Then run the validator:

```bash
python skills/csag-light/scripts/validate_csag_light.py \
  --artifact /path/to/artifact.json \
  --raw-paper /path/to/raw_paper.json
```

Do not accept the artifact until it passes.

If the artifact is close but fails on:

- non-canonical IDs
- type-label drift
- missing `evidence_link` provenance
- approximate quotes that can be repaired to exact substrings

then run:

```bash
python skills/csag-light/scripts/repair_csag_light.py \
  --artifact /path/to/rough_artifact.json \
  --raw-paper /path/to/raw_paper.json \
  --output /path/to/repaired_artifact.json
```

## Direct extraction fallback

When speed matters and you skip full CSAG materialization:

1. segment the paper by section
2. extract entities first
3. extract hypothesis/result/conclusion candidates
4. extract evidence items
5. build evidence links explicitly
6. only then assemble discoveries, gaps, and conclusions
7. assign confidence last

Do not do single-shot paper summarization and call it logic extraction.

## Microbial genomics notes

Prioritize these entity families when present:

- taxa
- strains and isolates
- genes and proteins
- operons and gene clusters
- plasmids, phages, MAGs, contigs, chromosomes
- pathways and phenotypes
- hosts, environments, and assays

Common evidence types worth preserving:

- homology
- synteny
- phylogeny
- comparative genomics
- expression
- knockout or perturbation
- biochemical assay
- cultivation or phenotype assay

## When to escalate back to full CSAG

Do not rely on `csag-light` alone when:

- multiple contexts materially change the claim
- conditional claims depend on time, dose, genotype, or environment
- assertion-to-assertion contradiction matters
- mechanistic inference chains are central to the paper
- detailed critique or bias analysis is needed

In those cases, retain or regenerate full CSAG and treat the compact export as a secondary view.
