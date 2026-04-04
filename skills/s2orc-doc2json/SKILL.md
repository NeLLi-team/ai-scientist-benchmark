---
name: s2orc-doc2json
description: Convert a local scientific PDF into S2ORC JSON and Markdown using the vendored s2orc-doc2json backend plus a local Grobid service. Use when an agent must process PDFs locally without OpenAI APIs and without conda.
---

# S2ORC DOC2JSON

Use this skill when a local scientific PDF should be converted with the repo's vendored `s2orc-doc2json/` backend instead of any remote OCR API.

Hard constraints for this skill:

- do not use OpenAI APIs
- do not use conda
- use only local tooling plus a local Grobid service

This skill is for PDF input. It does not cover DOCX.

## What this skill produces

The normal end state is:

- `<stem>.s2orc.json`
- `<stem>.tei.xml`
- `<stem>.md`

`<stem>.s2orc.json` is the source of truth.
`<stem>.md` is the readable derivative for downstream review and cleanup.

## Quick start

1. Create the local virtual environment:

   ```bash
   bash skills/s2orc-doc2json/scripts/bootstrap_local_env.sh
   ```

2. Activate it:

   ```bash
   source s2orc-doc2json/.venv/bin/activate
   ```

3. Confirm that Grobid is reachable:

   ```bash
   python skills/s2orc-doc2json/scripts/check_grobid.py
   ```

4. Convert the PDF:

   ```bash
   python skills/s2orc-doc2json/scripts/convert_pdf.py \
     /abs/path/to/paper.pdf \
     --output-dir /abs/path/to/output-dir
   ```

5. Review the Markdown against the JSON and apply cleanup only where the parse is clearly wrong.

## Completion rule

For this skill, the task is not complete when only one of the following exists:

- raw PDF only
- TEI only
- S2ORC JSON only

The task is complete when:

1. the PDF has been converted to S2ORC JSON
2. a Markdown derivative has been generated
3. the Markdown has been reviewed for obvious parse noise
4. the agent reports any major limitations honestly

## Workflow

### 1. Bootstrap a local Python environment

Use the bundled bootstrap script. It creates `s2orc-doc2json/.venv` with `python3 -m venv` and installs the vendored backend requirements.

Do not replace this with conda.

### 2. Ensure Grobid is available locally

The vendored backend expects a Grobid-compatible service on `127.0.0.1:8070` unless overridden.

The wrapper only needs these local API endpoints to work:

- `GET /api/isalive`
- `POST /api/processFulltextDocument`

Preferred order:

1. use an already-running local Grobid service
2. use a local Grobid install you can start directly
3. use a local containerized Grobid service if the machine supports it

The important requirement is local availability on a host and port you control. Do not switch to a hosted OCR API.

If the service is not on the default address, pass:

```bash
python skills/s2orc-doc2json/scripts/check_grobid.py --host 127.0.0.1 --port 8071

python skills/s2orc-doc2json/scripts/convert_pdf.py \
  /abs/path/to/paper.pdf \
  --output-dir /abs/path/to/output-dir \
  --grobid-server 127.0.0.1 \
  --grobid-port 8071
```

### 3. Convert the PDF

Use the high-level wrapper:

```bash
python skills/s2orc-doc2json/scripts/convert_pdf.py \
  /abs/path/to/paper.pdf \
  --output-dir /abs/path/to/output-dir
```

What it does:

1. runs the vendored PDF -> TEI -> S2ORC JSON conversion
2. copies the TEI XML into the output directory
3. renames the JSON artifact to `<stem>.s2orc.json`
4. renders a readable `<stem>.md`

By default the wrapper removes the scratch temp directory after it copies out the TEI file.

Use `--keep-temp` only if you need the intermediate temp tree for debugging.

### 4. Review the generated Markdown

Read these references when the output needs cleanup:

- `references/output-contract.md`
- `references/manual-cleanup-checklist.md`

Do not rewrite the paper into prettier prose.
Only fix obvious parser artifacts:

- repeated headers or footers
- broken section ordering
- duplicated paragraphs
- obvious line-break damage

Do not hallucinate missing text, equations, or citations.

## Wrapper details

The main wrapper script is:

- `skills/s2orc-doc2json/scripts/convert_pdf.py`

If you only need Markdown from an existing S2ORC JSON file:

```bash
python skills/s2orc-doc2json/scripts/s2orc_json_to_markdown.py \
  /abs/path/to/paper.s2orc.json \
  --output /abs/path/to/paper.md
```

## Recommended output layout

For a PDF named `paper.pdf`, prefer an output directory like:

```text
/abs/path/to/output-dir/
  paper.s2orc.json
  paper.tei.xml
  paper.md
```

For benchmark case work in this repo, the usual destination Markdown is:

- `ground_truth/<case>/data/<case>.md`

but the conversion helper itself is generic and can write anywhere.

## Failure handling

If conversion fails:

1. confirm the venv exists and dependencies installed
2. confirm Grobid is alive locally
3. rerun the wrapper with an explicit `--temp-dir`
4. inspect the copied TEI XML when available

Report failures concretely:

- Grobid unreachable
- Grobid returned an HTTP error
- TEI file missing after conversion
- JSON generation failed
- Markdown rendered but is structurally poor

Do not silently substitute a different backend.

## Important repo-local distinction

This repo also contains another PDF-to-Markdown skill wired to an OCR API outside the repo.

When the user asks for a local `s2orc-doc2json` workflow or says not to use remote APIs, use this skill instead.
