"""Render the assertion scorer's results as a Markdown report."""

from __future__ import annotations

from collections.abc import Sequence

from assertion_grading import (
    ANCHOR_NEGATION_MAX,
    ANCHOR_SOURCE_MIN,
    ANCHOR_UNRELATED_MAX,
    Reference,
)

__all__ = ["render_markdown"]

VERDICT_SYMBOLS = {"matched": "M", "partial": "P", "missing": "-", "contradicted": "X"}


def render_markdown(results: dict, references: Sequence[Reference]) -> str:
    """The ranked candidates, setups, anchor check and per-assertion verdict matrix."""
    lines = [f"# Assertion scoring: {results['case_id']}", ""]
    if results["provisional"]:
        reason = " ".join(str(results["signoff_problem"]).split())
        lines += [
            f"**PROVISIONAL: the ground truth has no valid sign-off.** {reason}",
            "",
        ]
    judges = ", ".join(
        f"{judge['name']} ({judge['model']}, {judge['cli_version']})"
        for judge in results["judges"]
    )
    lines += [
        f"Judges: {judges}",
        f"Ground truth sha256: `{results['ground_truth_sha256']}`",
        "",
    ]
    ranked = sorted(results["candidates"], key=lambda row: (-row["score"], row["id"]))
    return (
        "\n".join(
            [
                *lines,
                *_anchor_lines(results["anchors"]),
                *_candidate_lines(ranked),
                *_setup_lines(results["setups"]),
                *_matrix_lines(ranked, references),
            ]
        )
        + "\n"
    )


def _anchor_lines(anchors: dict) -> list[str]:
    if anchors["valid"] is None:
        return ["## Anchors", "", "Not run (`--anchors none`).", ""]
    status = "valid" if anchors["valid"] else "INVALID"
    lines = [
        "## Anchors",
        "",
        (
            f"Run is {status}. Expected: source >= {ANCHOR_SOURCE_MIN:g}, "
            f"unrelated <= {ANCHOR_UNRELATED_MAX:g}, "
            f"negation <= {ANCHOR_NEGATION_MAX:g} with most of the weight judged "
            "contradicted. Every anchor verdict must be complete and unflagged."
        ),
        "",
        "| Anchor | Mean | By judge |",
        "|---|---:|---|",
    ]
    for kind, per_judge in anchors["by_judge"].items():
        lines.append(f"| {kind} | {anchors[kind]} | {_by_judge(per_judge)} |")
    return [*lines, *(f"- {problem}" for problem in anchors["problems"]), ""]


def _candidate_lines(ranked: Sequence[dict]) -> list[str]:
    lines = [
        "## Candidates",
        "",
        (
            "| Rank | Candidate | Score | By judge | Agreement | Core "
            "| Contradicted | Keyword |"
        ),
        "|---:|---|---:|---|---:|---|---:|---:|",
    ]
    for rank, row in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | {row['id']} | {row['score']} "
            f"| {_by_judge(row['score_by_judge'])} | {row['agreement']} "
            f"| {row['core_coverage']} | {row['contradicted']} "
            f"| {row['keyword_score']} |"
        )
    return [*lines, ""]


def _setup_lines(setups: Sequence[dict]) -> list[str]:
    lines = [
        "## Setups",
        "",
        "| Setup | n | Mean | Min | Max |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {s['setup']} | {s['n']} | {s['mean']} | {s['min']} | {s['max']} |"
        for s in setups
    ]
    return [*lines, ""]


def _matrix_lines(ranked: Sequence[dict], references: Sequence[Reference]) -> list[str]:
    legend = ", ".join(f"{symbol} {name}" for name, symbol in VERDICT_SYMBOLS.items())
    lines = [
        "## Verdicts",
        "",
        f"One symbol per judge ({legend}); `*` marks a flagged verdict.",
        "",
        "| Assertion | Criticality | " + " | ".join(row["id"] for row in ranked) + " |",
        "|---|---|" + "---|" * len(ranked),
    ]
    for ref in references:
        cells = " | ".join(_cell(row, ref.assertion_id) for row in ranked)
        lines.append(f"| {ref.assertion_id} | {ref.criticality} | {cells} |")
    return [*lines, ""]


def _cell(row: dict, assertion_id: str) -> str:
    symbols = []
    for verdicts in row["verdicts"].values():
        verdict = verdicts[assertion_id]
        flag = "*" if verdict["flags"] else ""
        symbols.append(VERDICT_SYMBOLS[verdict["verdict"]] + flag)
    return "/".join(symbols)


def _by_judge(scores: dict[str, float]) -> str:
    return ", ".join(f"{judge} {score}" for judge, score in scores.items())
