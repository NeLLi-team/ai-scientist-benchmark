# Scientific Article Schema

This file condenses the contract from:

- `/home/fschulz/dev/gksyn/03pdfextraction/README.md`
- `/home/fschulz/dev/gksyn/03pdfextraction/schemas/article.yaml`
- `/home/fschulz/dev/gksyn/03pdfextraction/src/schemas/article_model.py`

Use it when the OCR input is a scientific paper.

## Required output shape

Create one JSON object with these keys and no extra top-level fields:

```json
{
  "title": "",
  "authors": "",
  "affiliations": "",
  "abstract": "",
  "main": "",
  "methods": "",
  "figure_legends": [],
  "figure_interpretation": "",
  "references": []
}
```

## Field semantics

- `title`
  - article title exactly as written
- `authors`
  - author list as a single comma-separated string
- `affiliations`
  - affiliations as a single comma-separated string
- `abstract`
  - abstract text verbatim when possible
- `methods`
  - Methods or Materials section text
- `figure_legends`
  - one item per figure or table caption
- `figure_interpretation`
  - concise narrative interpretation of figures or tables
  - only include claims actually supported by the OCR text and visible captions
- `references`
  - one entry per cited reference
- `main`
  - one combined narrative field for the core article body

## Main field composition rule

The Pydantic model in `article_model.py` uses internal fields:

- `introduction`
- `results`
- `discussion`
- `conclusion`

Those are **internal only** and should **not** appear in the final JSON.

Instead:

- identify those sections if possible
- concatenate them in reading order with blank lines between sections
- write the result into `main`

If the paper uses different section names, map equivalent narrative sections into `main`.

## Practical extraction rules

- Prefer fidelity over rewriting.
- Keep headings and section order when they help preserve structure.
- Deduplicate obvious repeated headers, footers, and page numbers before structuring.
- Do not invent missing sections.
- If there is no explicit methods section, leave `methods` empty rather than stuffing unrelated text into it.
- If figure captions are not recoverable, use an empty list for `figure_legends`.
- If figure interpretation would require guessing, leave `figure_interpretation` empty.

## Minimum content expectation for papers

For a real scientific paper, these should normally be recoverable and therefore non-empty:

- `title`
- `authors`
- `main`

Usually also expected when recoverable:

- `affiliations`
- `abstract`
- `references`

## Validation mindset

The `03pdfextraction` pipeline validates against both:

- LinkML schema: `schemas/article.yaml`
- Pydantic model: `src/schemas/article_model.py`

For this skill, stay compatible with both by:

- using the exact top-level keys above
- keeping string fields as strings
- keeping list fields as arrays of strings
- omitting no required keys
- treating the scaffold as a starting point only, not a completed result
