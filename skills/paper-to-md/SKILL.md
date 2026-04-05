---
name: pdf-to-md
description: Convert local PDF files to Markdown through the local OCR API service. For scientific papers, the required end state is not just Markdown: convert through the API first, then structure the OCR result into the local Article schema references and validate the populated article JSON.
---

# PDF To Markdown

Use this skill when a local PDF should be converted to Markdown through the OCR API service on this machine.

This repo-local skill is self-contained under `skills/paper-to-md/`.

## Runtime setup

From the repo root:

```bash
uv sync
```

Run the helper scripts with:

```bash
uv run python ...
```

This skill still depends on:

- the local OCR API service at `http://127.0.0.1:8002/ocr`
- a valid `OCR_API_KEY` or equivalent API access
- `curl` on `PATH`

This skill has two modes:

1. Generic document mode:
   Produce Markdown only.
2. Scientific literature mode:
   Convert the PDF to Markdown through the OCR API first, then structure that Markdown into a populated schema-aligned article JSON for papers, preprints, and journal articles.

## Completion rule

For a scientific paper, the task is not complete after OCR and is not complete after a blank scaffold.

The minimum complete workflow is:

1. convert the PDF to Markdown through the OCR API
2. create a `section_audit.json` that states which schema sections were detected and which are still missing
3. create a schema-aligned article JSON from that audit
4. validate the populated JSON against both the schema shape and the section audit

Markdown alone is insufficient for a scientific-paper task under this skill.

## OCR API path

The OCR API path is:

- local base URL: `http://127.0.0.1:8002/ocr`
- public-compatible path prefix: `/ocr/...`

Important note:

- the current default backend behind this API is LightOnOCR-2-1B on the A6000

The API is served locally by:

- `/home/fschulz/dev/nelli-website/api/ocr/api_server.py`

That API server shells out to the OCR wrapper at:

- `/home/fschulz/bester-hosting/services/lighton-ocr/scripts/convert_document.py`

That wrapper uses the local LightOn OCR model service at:

- `http://127.0.0.1:8011/v1/models`
- container/service root: `/home/fschulz/bester-hosting/services/lighton-ocr/`

## Authentication

The OCR API expects `X-API-Key`.

Default environment variable for the helper script:

- `OCR_API_KEY`

If that is not set, either:

- pass `--api-key` directly to the helper script
- or create/use a valid API key for the OCR API service

Important auth note:

- this OCR API now uses the **shared** Nelli API-key database with `polars-dovmed`
- a valid `polars-dovmed` key should also work for `/ocr/api/jobs`
- OCR async job state remains local to the OCR service; only the API-key store is shared

Both services use:

- the same `X-API-Key` header
- the same SQLite `api_keys` schema
- the same shared key database after the auth unification change

## Workflow

1. Submit the file to the OCR API with:

   ```bash
   uv run python skills/paper-to-md/scripts/ocr_api_job.py \
     /path/to/input.pdf \
     --output-dir /path/to/output-dir
   ```

2. Read the downloaded Markdown file.

3. If the document is scientific literature, read:

   - `references/gksyn_article_schema.md`
   - `references/article.yaml`

4. Build the mandatory section audit with:

   ```bash
   uv run python skills/paper-to-md/scripts/build_section_audit.py \
     /path/to/output-dir/document.md
   ```

   This writes:

   - `<stem>.section_audit.json`

   The section audit is the machine-readable questionnaire for the agent. It records:

   - which headings were detected
   - which schema fields are expected from this document
   - which fields are already populated or still missing
   - which source headings or front-matter regions justify each field

5. Create a first-pass populated schema JSON from the OCR Markdown with:

   ```bash
   uv run python skills/paper-to-md/scripts/populate_article_json.py \
     /path/to/output-dir/document.md
   ```

   Notes:

   - this step may create `<stem>.article.json` directly
   - the populate helper is a starting point, not a substitute for review

   Important note:

   - this step also refreshes `<stem>.section_audit.json`

6. Review and refine both:

   - `<stem>.section_audit.json`
   - `<stem>.article.json`

   If the paper exposes figure or table captions, render the relevant PDF pages to PNG with the repo-local helper:

   ```bash
   uv run python skills/paper-to-md/scripts/render_pdf_pages_to_png.py \
     /path/to/input.pdf \
     --output-dir /path/to/output-dir/figure_review
   ```

   Then inspect the rendered PNGs directly before finalizing `figure_interpretation`.

   Important requirement:

   - do **not** stop after generating the section audit only
   - do **not** stop after generating Markdown only
   - for scientific papers with figure or table captions, the task is not complete until `figure_interpretation` has been filled from direct figure review or an explicit no-interpretation exception is justified
   - for scientific papers, the task is only complete once the JSON is filled from the Markdown and validated against the section audit

7. Validate the populated JSON with:

   ```bash
   uv run python skills/paper-to-md/scripts/validate_article_json.py \
     /path/to/output-dir/document.article.json \
     --scientific-paper \
     --section-audit /path/to/output-dir/document.section_audit.json
   ```

8. If validation fails, fix the JSON and rerun validation.

## Scientific literature rule

Treat these as scientific literature by default:

- journal articles
- preprints
- conference papers
- methods papers
- review articles
- manuscript PDFs

For those documents, you must produce all of:

- `<stem>.md`
- `<stem>.ocr.json`
- `<stem>.job.json`
- `<stem>.section_audit.json`
- `<stem>.article.json`

The paper workflow is not complete if:

- `section_audit.json` is missing
- `article.json` is missing
- `article.json` has not been validated against the section audit
- `figure_interpretation` is empty even though figure/table captions were recoverable from the paper
- the work stopped at Markdown without schema structuring

## Expected outputs

The OCR helper writes these into the chosen output directory:

- `<stem>.md`
- `<stem>.ocr.json`
- `<stem>.job.json`

For scientific papers, add and validate:

- `<stem>.section_audit.json`
- `<stem>.article.json`

Required when figures or tables are recoverable:

- `figure_review/` with rendered PDF page PNGs produced by `skills/paper-to-md/scripts/render_pdf_pages_to_png.py`

## Structuring guidance

When creating `<stem>.article.json`:

- follow the exact output keys in `references/gksyn_article_schema.md`
- keep the output consistent with `references/article.yaml`
- do not emit internal fields like `introduction`, `results`, `discussion`, or `conclusion` as top-level JSON fields
- instead, compose those into the single `main` field
- if a field cannot be recovered confidently, prefer:
  - `""` for string fields
  - `[]` for list fields
  over hallucinating content

## Expected section mapping for papers

When structuring the OCR Markdown into the schema:

- `title`
  - use the article title, not the journal banner or generic labels like `RESEARCH ARTICLE`
- `authors`
  - extract the author line as a single comma-separated string
- `affiliations`
  - extract affiliation lines as a single comma-separated string
- `abstract`
  - map the abstract section directly when present
  - if no explicit `Abstract` heading exists, recover it from front matter before the first body section when the text supports that interpretation
- `main`
  - combine the narrative body sections such as introduction, results, discussion, and conclusion in reading order
- `methods`
  - map methods or materials-and-methods content only
  - numbered headings like `# 2 Methods` or `## 2.3 Genomic characterization ...` still count after heading normalization
- `figure_legends`
  - capture figure or table captions as one list item per caption
  - support caption starters like `Fig.`, `Figure`, `Table`, `Supplementary Figure`, and `Supplementary Table`
- `figure_interpretation`
  - populate this for scientific papers when figures or tables are recoverable
  - ground it in the OCR text plus reviewed figure page PNGs
  - do not guess beyond what the captions and visible figure content support
- `references`
  - extract one entry per cited item when a references section is present

## Minimum content rule for scientific papers

For a scientific paper JSON, at minimum these should normally be non-empty unless the OCR itself is severely degraded:

- `title`
- `authors`
- `main`

Usually also non-empty when recoverable:

- `affiliations`
- `abstract`
- `methods`
- `figure_legends`
- `figure_interpretation`
- `references`

If the OCR Markdown clearly contains one of these and the JSON field is still empty, the output is not complete.

## Section audit rule

The section audit is mandatory for scientific papers.

It exists to stop the exact failure mode where:

- the OCR Markdown is good
- but the schema JSON validates while leaving key sections empty

Use the section audit to verify:

- whether the document appears to contain an abstract
- whether methods headings were detected
- whether a references section was detected
- whether caption-like lines were detected
- whether affiliations were detected in front matter

If the section audit says a field is expected, the final `article.json` must populate it or the validation should fail.

## OCR cleanup before structuring

Before populating the article JSON:

- remove repeated page furniture when it is clearly duplicated across pages:
  - page headers
  - page footers
  - page numbers
  - repeated copyright notices
- preserve scientific content verbatim where possible
- do not aggressively rewrite equations, references, or headings just to make them prettier

## Repo-local files

The repo-local files for this skill are:

- `skills/paper-to-md/references/gksyn_article_schema.md`
- `skills/paper-to-md/references/article.yaml`
- `skills/paper-to-md/scripts/ocr_api_job.py`
- `skills/paper-to-md/scripts/build_section_audit.py`
- `skills/paper-to-md/scripts/populate_article_json.py`
- `skills/paper-to-md/scripts/validate_article_json.py`
- `skills/paper-to-md/scripts/render_pdf_pages_to_png.py`
- `skills/paper-to-md/scripts/article_extraction.py`

The condensed instructions needed for normal use are already captured in `references/gksyn_article_schema.md`. Use the local copied `references/article.yaml` when you need the exact LinkML shape. Only read the upstream source files directly if you need to verify edge cases beyond the local references.
