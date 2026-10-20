---
name: benchmark-scoring
description: Build the evaluator-facing scoring package for one case from the ground-truth CSAG artifact. Produces a machine-readable scoring schema, human-readable rubric, evaluator instructions, and evaluator export.
---

# Benchmark Scoring

Use this skill when a benchmark-authoring case already has:

- a ground-truth CSAG artifact under `ground_truth/<case>/csag/`
- a participant research question under `ground_truth/<case>/prompt/`
- a starting-data manifest under `ground_truth/<case>/starting_data/`

and the repo needs the **evaluator-facing scoring package**.

## Runtime setup

From the repo root:

```bash
uv sync
```

Run the helper with:

```bash
uv run python ...
```

## Required outputs

For one case `<case>`, produce:

- `ground_truth/<case>/scoring/scoring_schema.json`
- `ground_truth/<case>/scoring/scoring_rubric.md`
- `ground_truth/<case>/scoring/evaluator_instructions.md`
- `ground_truth/<case>/exports/evaluator/`

## Core rule

The scoring package is derived from the ground-truth knowledge artifact and is not participant-visible.

## Script

Use the helper script:

```bash
uv run python skills/benchmark-scoring/scripts/build_scoring_package.py \
  /abs/path/to/ground_truth/<case>
```

## Scoring candidate CSAGs

`scripts/score_csag_candidates.py` scores candidate `*_csag.json` files (with their `*_csag.validation.json`) against a case's scoring schema and writes ranked tables:

```bash
uv run python skills/benchmark-scoring/scripts/score_csag_candidates.py \
  --case-dir ground_truth/<case> \
  --candidate-dir <dir-of-candidate-csags> \
  --out-dir <output-dir>
```

Known limits: its claim, method, entity, dataset, quantitative and limitation rules are hand-written for `06mega`, and it matches keywords as substrings over the flattened candidate CSAG text. Short tokens can match inside other words and negated statements still earn credit, so treat totals as a ranking rather than calibrated accuracy. Blinded candidates must be mapped back to original identifiers before scoring, because the dataset and entity rules look for the original IMG/M IDs.

## Reviewing the ground truth

A researcher reviews and signs the ground-truth assertions before assertion-level scoring. Agents write the review packet; only a researcher runs `sign`, and an agent must not create `signoff.json` itself.

```bash
uv run python skills/benchmark-scoring/scripts/review_ground_truth.py packet \
  --case-dir ground_truth/<case> --out <sheet.md>
# the researcher fills decision:, criticality: and note: in every section
uv run python skills/benchmark-scoring/scripts/review_ground_truth.py sign \
  --case-dir ground_truth/<case> --sheet <sheet.md> --reviewer "<name>"
```

- The sheet has one `## <label>` section per assertion: text, `claim_role`, current criticality, falsification criteria, source quotes, and each linked evidence item with its polarity, strength and quoted spans. It ends with blank `decision:` (`approve`, `edit` or `reject`), a `criticality:` line that starts at the current value (`core`, `major`, `supporting`, `background`) and `note:`.
- `sign` refuses (exit 2, nothing written) if a decision is blank or unknown, a criticality is invalid, an `approve` changes the criticality (use `edit`), a section is missing, repeated or unknown, a section repeats its `decision:` or `criticality:` line, or the sheet was made from a different `paper_extraction.json`. A blank criticality line means the current value.
- `<case>/csag/signoff.json`: `{"case_id", "ground_truth_sha256", "reviewer", "signed_at", "assertions": {"A0011": {"decision", "criticality", "note"}}}`, keyed by assertion label. The hash is the SHA-256 of `paper_extraction.json`, so any later edit of the ground truth invalidates the sign-off. Signing again replaces the file.
- `edit` and `reject` change scoring only through `criticality` and the dropped assertions; a text change goes into `paper_extraction.json` itself, and then the sheet must be made and signed again.

## Assertion-level scoring

`scripts/score_assertions.py` scores candidate `*_csag.json` files against the signed ground-truth CSAG, one verdict per ground-truth assertion, with two judge models. The keyword scorer above stays as a cross-check.

```bash
uv run python skills/benchmark-scoring/scripts/score_assertions.py \
  --case-dir ground_truth/<case> --candidate-dir <dir-of-candidate-csags> --out-dir <output-dir> \
  [--judges sonnet,astra] [--require-signoff] [--anchors default|none] [--unrelated-csag PATH] [--cached-only]
```

- **Gate.** The scorer reads `paper_extraction.json` once and hashes and parses those same bytes. A valid sign-off is `<case>/csag/signoff.json` with a `ground_truth_sha256` equal to that hash that passes the checks of `review_ground_truth.py sign`: a reviewer, an ISO-8601 `signed_at`, a `case_id` equal to the case directory name, one decision for every assertion (`approve`, `edit` or `reject`) and a valid criticality, and an `approve` that keeps the current criticality. If the sign-off is missing, stale, incomplete or invalid, the scorer still runs by default: it logs a warning, ignores the sign-off, marks every result `"provisional": true` and records the reason in `signoff_problem`. `--require-signoff` restores the refusal (exit 2, no judge call) for strict runs. A valid sign-off applies the criticality edits and drops `reject` assertions.
- **Judges.** `sonnet` runs `claude -p --model sonnet --effort xhigh --safe-mode --tools "" --no-session-persistence --strict-mcp-config --setting-sources "" --output-format json --json-schema <schema>`. `astra` runs `codex exec --skip-git-repo-check --sandbox read-only -m gpt-6-astra -c model_reasoning_effort=xhigh --disable shell_tool --json --output-schema <file> -o <file>`. The packet goes in on stdin. Each call runs in a fresh empty temp directory with only `PATH`, `HOME`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR` and `CLAUDE_CONFIG_DIR` set. Both use the host's login. Codex gets a private HOME whose `auth.json` links to the host file, so your `~/.codex` config, `AGENTS.md` and skills stay out of the judge; if Codex replaces the file (a token refresh), the new file is installed over the host login, also after a failed call, only if it is valid JSON that keeps every key of the current login and differs from it. The install holds an exclusive lock (`auth.json.judge-lock` beside the login), stages a uniquely named file in the host directory with mode 0600 and renames it over the login, so the host login is never truncated or half written. A login that lives in an environment variable such as `ANTHROPIC_API_KEY` is not passed on. No code prints or logs login contents.
- **Packet and verdicts.** The packet holds the ground-truth assertions (id, text, criticality, falsification criteria, linked evidence text) and the candidate's assertions, evidence items and links under neutral ids (`CA01`, `CE01`). It has no candidate title, id, provenance or file name. The judge returns `matched`, `partial`, `missing` or `contradicted` for every assertion, with candidate ids, a quote and a reason. The scorer checks every entry itself. An assertion becomes `missing` with a flag if the judge gave it no entry, two or more entries (whatever their order), an entry with a missing or mistyped field, an unknown label, a quote that is not in the candidate text (whitespace-normalised), or no candidate id that exists in the candidate CSAG. A cited id that is not in the candidate is dropped and flagged; the verdict keeps its label while at least one cited id is real. Entries for unknown assertion ids are ignored and flagged.
- **Score.** Credit is matched 1, partial 0.5, missing 0, contradicted -1, weighted core 2.0, major 1.5, supporting 1.0, background 0.5: `max(0, 100 * sum(w * credit) / sum(w))`, per judge and as the mean of judges. `agreement` is the share of assertions with the same verdict from every judge (`null` for one judge). `core_coverage` is the mean number of core assertions judged matched or partial. `contradicted` counts assertions that any judge marked contradicted. `keyword_score` is `score_csag_candidates.score_candidate` for the same file.
- **Anchors** (default on, scored by the same judges). `source` is the ground truth itself (expect >= 90). `unrelated` is `--unrelated-csag`, default `ground_truth/testpdf/csag/paper_extraction.json` (expect <= 10). `negation` is the ground truth with each assertion text prefixed `It is not true that:` and no evidence (expect <= 5); evidence is dropped because its text restates the reference findings and a judge could read those instead of the denials. An anchor run is also invalid if any judge's verdicts for an anchor are incomplete or flagged, or if a judge did not mark a majority of the assertion weight in the `negation` anchor `contradicted`. If any check fails the run has `"valid": false` and the report says which.
- **Repeats.** Files named `<setup>__r01_csag.json` are grouped by setup; `setups` in `results.json` has `n`, `mean`, `min` and `max`.
- **Outputs.** `results.json`, `results.md` (ranked candidates, anchors, setups, verdict matrix) and `judgments/<candidate>.<judge>.json` with the packet hash, CLI version, reported model, token usage, command, answer and raw stdout. A call is stored for reuse only after its answer parses as a verdict list; otherwise it is kept as `<candidate>.<judge>.invalid-N.json` and the run stops. A rerun skips every call whose stored packet hash matches and whose answer still parses; an unusable stored answer is renamed the same way and requested again. `--cached-only` makes no judge call: it re-aggregates from `judgments/` (copy the directory to a new `--out-dir` to keep the old results) and exits 1 if a call is not stored. Exit codes: 0 done, 1 a judge call failed (finished calls stay in `judgments/`), 2 refused input or gate.
- **Checked 2026-10-19** with `claude` 2.1.303 and `codex-cli` 0.163.0: one two-assertion call per judge returned the schema JSON; Claude reported `claude-sonnet-5-5`, Codex `gpt-6-astra`.

Known limits: calls run one at a time at `xhigh` effort, so a full panel is slow. Candidate text can still name its own setup (for example a manuscript that says "Claude Code"), because only metadata is removed. The Codex read-only sandbox still lets a model read host files outside HOME; `--disable shell_tool` is there to remove the shell, and its effect is not tested. Tests use stub `claude` and `codex` executables (`tests/fixtures/stub_judge.py`) and call no model.
