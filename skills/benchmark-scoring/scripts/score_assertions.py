#!/usr/bin/env python3
"""Score candidate CSAGs against a signed ground truth, assertion by assertion.

Judge models label every ground-truth assertion as matched, partial, missing or
contradicted for each candidate. The scorer checks the labels, weights them by
criticality and reports per-judge and mean scores, judge agreement, repeat
statistics and anchor checks. A reviewer signs the ground truth with
`review_ground_truth.py`. Without a valid sign-off the scorer still runs, warns and
marks every result provisional; `--require-signoff` makes it refuse instead.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import re
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import assertion_judges
import score_csag_candidates
from assertion_grading import (
    ANCHOR_NEGATION_CONTRADICTED_MIN,
    WEIGHTS,
    Packet,
    Reference,
    Verdict,
    agreement,
    anchor_problems,
    build_packet,
    contradicted_weight_share,
    credit_score,
    raw_credit,
    validate_verdicts,
)
from assertion_judges import Judge, JudgeError
from assertion_report import render_markdown
from ground_truth_signoff import SignoffError, read_signoff

logger = logging.getLogger("score_assertions")

NEGATION_PREFIX = "It is not true that: "
DEFAULT_UNRELATED = Path("testpdf") / "csag" / "paper_extraction.json"
DEFAULT_JUDGES = "sonnet,astra"
ANCHOR_KINDS = ("source", "unrelated", "negation")
REPEAT_NAME = re.compile(r"^(?P<setup>.+)__(?P<run>r\d+)$")


class ScoringError(ValueError):
    """Raised when the inputs or the sign-off gate forbid a scoring run."""


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """The ground-truth CSAG, its hash, sign-off state and graded assertions.

    `problem` says why the sign-off was not used; it is None when `signed`.
    """

    csag: dict
    sha256: str
    signed: bool
    problem: str | None
    references: tuple[Reference, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    """A CSAG to score. `keyword_score` is None for the anchors, which have no file."""

    id: str
    setup: str
    run: str | None
    csag: dict
    keyword_score: float | None = None


@dataclass(frozen=True, slots=True)
class Judged:
    """Checked verdicts, reported models and problems for one candidate, by judge."""

    candidate: Candidate
    verdicts: dict[str, dict[str, Verdict]]
    models: dict[str, str | None]
    flags: list[str]


@dataclass(frozen=True, slots=True)
class Panel:
    """The judges for one run, their CLI versions and where their calls are stored."""

    judges: tuple[Judge, ...]
    versions: Mapping[str, str]
    judgments: Path
    cached_only: bool


def load_ground_truth(case_dir: Path, *, require_signoff: bool) -> GroundTruth:
    """Read the ground-truth CSAG and apply the reviewer's sign-off.

    The file is read once, so the hash covers exactly the bytes that are scored.
    A sign-off that is missing, stale, incomplete or invalid is set aside with a
    warning and the result is provisional, with the reason kept. A valid one
    applies its criticality edits and drops rejected assertions.

    Raises:
        ScoringError: The ground truth is unreadable, the sign-off is unusable
            and `require_signoff` is true, or an assertion has no usable
            criticality.
    """
    truth_path = case_dir / "csag" / "paper_extraction.json"
    try:
        raw = truth_path.read_bytes()
    except OSError as err:
        raise ScoringError(f"cannot read the ground-truth CSAG {truth_path}") from err
    sha256 = hashlib.sha256(raw).hexdigest()
    csag = json.loads(raw)
    current = {a["label"]: a.get("criticality", "") for a in csag["assertions"]}
    try:
        decisions = read_signoff(
            case_dir / "csag" / "signoff.json",
            sha256=sha256,
            case_id=case_dir.name,
            current_criticality=current,
        )
    except SignoffError as err:
        if require_signoff:
            raise ScoringError(f"{err}; sign the ground truth first") from err
        logger.warning("%s; continuing, results are provisional", err)
        decisions, problem = None, str(err)
    else:
        problem = None
    references = _references(csag, decisions)
    if not references:
        raise ScoringError("every ground-truth assertion was rejected in the sign-off")
    return GroundTruth(csag, sha256, decisions is not None, problem, references)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scorer; return 0 on success, 2 on refused input, 1 on a judge failure."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    try:
        run(args)
    except ScoringError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except JudgeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


def run(args: argparse.Namespace) -> None:
    """Gate, judge every candidate and anchor, then write the results."""
    case_dir = args.case_dir.resolve()
    judges = _select_judges(args.judges)
    truth = load_ground_truth(case_dir, require_signoff=args.require_signoff)
    schema = _keyword_schema(case_dir)
    candidates = _load_candidates(args.candidate_dir, schema)
    anchors = []
    if args.anchors == "default":
        unrelated = args.unrelated_csag or case_dir.parent / DEFAULT_UNRELATED
        anchors = _anchor_candidates(truth, unrelated)
    judgments = args.out_dir / "judgments"
    judgments.mkdir(parents=True, exist_ok=True)
    versions = {judge.name: judge.version() for judge in judges}
    panel = Panel(tuple(judges), versions, judgments, args.cached_only)
    judged = [_judge_candidate(item, truth, panel) for item in candidates]
    judged_anchors = {item.id: _judge_candidate(item, truth, panel) for item in anchors}
    judge_rows = [
        _judge_row(name, versions[name], [*judged, *judged_anchors.values()])
        for name in versions
    ]
    results = _results(case_dir.name, truth, judge_rows, judged, judged_anchors)
    _write_text(args.out_dir / "results.json", json.dumps(results, indent=2) + "\n")
    _write_text(args.out_dir / "results.md", render_markdown(results, truth.references))
    logger.info("wrote %s", args.out_dir / "results.json")
    if results["anchors"]["valid"] is False:
        logger.warning(
            "anchor check failed: %s", "; ".join(results["anchors"]["problems"])
        )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--case-dir", type=Path, required=True, help="ground_truth/<case>."
    )
    parser.add_argument(
        "--candidate-dir", type=Path, required=True, help="Directory of *_csag.json."
    )
    parser.add_argument("--out-dir", type=Path, required=True, help="Where results go.")
    parser.add_argument(
        "--judges", default=DEFAULT_JUDGES, help="Comma list: sonnet,astra."
    )
    parser.add_argument(
        "--require-signoff",
        action="store_true",
        help="Refuse to run (exit 2) without a valid sign-off. By default the "
        "scorer warns and marks every result provisional.",
    )
    parser.add_argument("--anchors", choices=("default", "none"), default="default")
    parser.add_argument(
        "--cached-only",
        action="store_true",
        help="Make no judge call; use stored judgments or stop.",
    )
    parser.add_argument(
        "--unrelated-csag",
        type=Path,
        default=None,
        help="CSAG of an unrelated paper for the unrelated anchor "
        f"(default: <ground_truth>/{DEFAULT_UNRELATED}).",
    )
    return parser.parse_args(argv)


def _select_judges(names: str) -> list[Judge]:
    selected = [name.strip() for name in names.split(",") if name.strip()]
    unknown = [name for name in selected if name not in assertion_judges.JUDGES]
    if unknown or not selected:
        known = ", ".join(assertion_judges.JUDGES)
        raise ScoringError(f"unknown judge {unknown or names!r}; choose from {known}")
    return [assertion_judges.JUDGES[name] for name in selected]


def _references(csag: dict, decisions: dict | None) -> tuple[Reference, ...]:
    texts = {
        i["id"]: i.get("evidence_text", "") for i in csag.get("evidence_items", [])
    }
    evidence: dict[str, list[str]] = {}
    for link in csag.get("evidence_links", []):
        evidence.setdefault(link["assertion"], []).append(
            texts.get(link["evidence_item"], "")
        )
    references = []
    for assertion in csag["assertions"]:
        label = assertion["label"]
        criticality = assertion.get("criticality")
        if decisions is not None:
            if decisions[label]["decision"] == "reject":
                continue
            criticality = decisions[label]["criticality"]
        if criticality not in WEIGHTS:
            raise ScoringError(
                f"assertion {label} has criticality {criticality!r}; "
                f"use one of {list(WEIGHTS)}"
            )
        references.append(
            Reference(
                assertion_id=label,
                text=assertion["assertion_text"],
                criticality=criticality,
                falsification_criteria=tuple(
                    assertion.get("falsification_criteria", [])
                ),
                evidence_texts=tuple(evidence.get(assertion["id"], [])),
            )
        )
    return tuple(references)


def _keyword_schema(case_dir: Path) -> dict:
    path = case_dir / "scoring" / "scoring_schema.json"
    if not path.exists():
        raise ScoringError(f"no scoring schema at {path} for the keyword cross-check")
    return score_csag_candidates.load_json(path)


def _load_candidates(candidate_dir: Path, schema: dict) -> list[Candidate]:
    paths = sorted(candidate_dir.glob("*_csag.json"))
    if not paths:
        raise ScoringError(f"no *_csag.json files in {candidate_dir}")
    candidates = []
    for path in paths:
        name = path.name.removesuffix("_csag.json")
        repeat = REPEAT_NAME.match(name)
        setup, run = (repeat["setup"], repeat["run"]) if repeat else (name, None)
        csag = json.loads(path.read_text(encoding="utf-8"))
        keyword = score_csag_candidates.score_candidate(path, schema)["total_score"]
        candidates.append(Candidate(name, setup, run, csag, keyword))
    return candidates


def _anchor_candidates(truth: GroundTruth, unrelated_path: Path) -> list[Candidate]:
    """The three anchors: the ground truth itself, an unrelated paper, its negation."""
    if not unrelated_path.exists():
        raise ScoringError(
            f"no unrelated CSAG at {unrelated_path}; pass --unrelated-csag"
        )
    csags = {
        "source": truth.csag,
        "unrelated": json.loads(unrelated_path.read_text(encoding="utf-8")),
        "negation": _negated(truth.csag),
    }
    return [
        Candidate(f"anchor-{kind}", f"anchor-{kind}", None, csags[kind])
        for kind in ANCHOR_KINDS
    ]


def _negated(csag: dict) -> dict:
    """A copy of the CSAG that only denies each assertion.

    Evidence is dropped: its text restates the reference findings, and a judge
    could read the anchor's position from that evidence instead of the denials.
    """
    negated = copy.deepcopy(csag)
    for assertion in negated["assertions"]:
        assertion["assertion_text"] = NEGATION_PREFIX + assertion["assertion_text"]
    negated["evidence_items"] = []
    negated["evidence_links"] = []
    return negated


def _judge_candidate(candidate: Candidate, truth: GroundTruth, panel: Panel) -> Judged:
    packet = build_packet(truth.references, candidate.csag)
    verdicts: dict[str, dict[str, Verdict]] = {}
    models: dict[str, str | None] = {}
    flags: list[str] = []
    for judge in panel.judges:
        record = _judgment_record(candidate.id, judge, packet, panel)
        models[judge.name] = record["model"]
        entries = assertion_judges.verdict_entries(judge.name, record["raw_output"])
        verdicts[judge.name], problems = validate_verdicts(
            entries, truth.references, packet
        )
        flags.extend(f"{judge.name}: {problem}" for problem in problems)
    return Judged(candidate, verdicts, models, flags)


def _judgment_record(
    candidate_id: str, judge: Judge, packet: Packet, panel: Panel
) -> dict:
    """Load the stored judge call for this packet, or make and store it.

    A call is stored as reusable only after its answer parses as a verdict list.
    A stored answer that no longer does is renamed `*.invalid-N.json`, kept for
    audit, and requested again.
    """
    path = panel.judgments / f"{candidate_id}.{judge.name}.json"
    packet_sha256 = hashlib.sha256(packet.text.encode("utf-8")).hexdigest()
    stored = _stored_record(path, packet_sha256, judge.name)
    if stored is not None:
        logger.info("reusing %s", path.name)
        return stored
    if panel.cached_only:
        raise JudgeError(
            f"--cached-only: no usable stored judgment for {candidate_id} "
            f"by {judge.name}"
        )
    logger.info("judging %s with %s", candidate_id, judge.name)
    call = judge.run(packet.text)
    record = {
        "candidate": candidate_id,
        "judge": judge.name,
        "packet_sha256": packet_sha256,
        "cli_version": panel.versions[judge.name],
        "model": call.model,
        "usage": call.usage,
        "command": list(call.command),
        "duration_s": call.duration_s,
        "raw_output": call.answer,
        "cli_stdout": call.cli_stdout,
    }
    try:
        assertion_judges.verdict_entries(judge.name, call.answer)
    except JudgeError:
        _write_text(_audit_path(path), json.dumps(record, indent=2) + "\n")
        raise
    _write_text(path, json.dumps(record, indent=2) + "\n")
    return record


def _stored_record(path: Path, packet_sha256: str, judge_name: str) -> dict | None:
    """The stored call if it is for this packet and its answer is usable."""
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored["packet_sha256"] != packet_sha256:
            return None
        assertion_judges.verdict_entries(judge_name, stored["raw_output"])
    except (json.JSONDecodeError, KeyError, TypeError, JudgeError) as err:
        audit = _audit_path(path)
        logger.warning(
            "setting aside the unusable %s as %s (%s)", path.name, audit.name, err
        )
        path.replace(audit)
        return None
    return stored


def _audit_path(path: Path) -> Path:
    """The first unused `<name>.invalid-N.json` beside a judgment file."""
    number = 1
    while (audit := path.with_name(f"{path.stem}.invalid-{number}.json")).exists():
        number += 1
    return audit


def _judge_row(name: str, version: str, judged: Sequence[Judged]) -> dict:
    """The judge's CLI version and the models its calls in this run reported."""
    models = sorted({model for item in judged if (model := item.models[name])})
    return {"name": name, "cli_version": version, "model": ",".join(models) or None}


def _write_text(path: Path, text: str) -> None:
    """Write through a temporary file so an interrupted run leaves no partial file."""
    staged = path.with_name(path.name + ".tmp")
    staged.write_text(text, encoding="utf-8")
    staged.replace(path)


def _results(
    case_id: str,
    truth: GroundTruth,
    judge_rows: list[dict],
    judged: Sequence[Judged],
    anchors: Mapping[str, Judged],
) -> dict:
    provisional = not truth.signed
    rows = [_candidate_row(item, truth, provisional=provisional) for item in judged]
    return {
        "case_id": case_id,
        "ground_truth_sha256": truth.sha256,
        "signed": truth.signed,
        "provisional": provisional,
        "signoff_problem": truth.problem,
        "judges": judge_rows,
        "anchors": _anchor_row(anchors, truth),
        "candidates": rows,
        "setups": _setup_rows(rows, provisional=provisional),
    }


def _candidate_row(judged: Judged, truth: GroundTruth, *, provisional: bool) -> dict:
    candidate = judged.candidate
    by_judge = _scores(judged, truth)
    core_ids = [r.assertion_id for r in truth.references if r.criticality == "core"]
    covered = [
        sum(verdicts[i].verdict in ("matched", "partial") for i in core_ids)
        for verdicts in judged.verdicts.values()
    ]
    contradicted = {
        assertion_id
        for verdicts in judged.verdicts.values()
        for assertion_id, verdict in verdicts.items()
        if verdict.verdict == "contradicted"
    }
    return {
        "id": candidate.id,
        "setup": candidate.setup,
        "run": candidate.run,
        "provisional": provisional,
        "score_by_judge": by_judge,
        "score": _mean(list(by_judge.values())),
        "agreement": agreement(judged.verdicts),
        "core_coverage": f"{_mean(covered):g}/{len(core_ids)}",
        "contradicted": len(contradicted),
        "keyword_score": candidate.keyword_score,
        "flags": judged.flags,
        "verdicts": {
            judge: {i: _verdict_dict(v) for i, v in verdicts.items()}
            for judge, verdicts in judged.verdicts.items()
        },
    }


def _verdict_dict(verdict: Verdict) -> dict:
    return {
        "verdict": verdict.verdict,
        "candidate_ids": list(verdict.candidate_ids),
        "quote": verdict.quote,
        "reason": verdict.reason,
        "flags": list(verdict.flags),
    }


def _scores(judged: Judged, truth: GroundTruth) -> dict[str, float]:
    return {
        judge: round(credit_score(truth.references, verdicts), 2)
        for judge, verdicts in judged.verdicts.items()
    }


def _anchor_row(anchors: Mapping[str, Judged], truth: GroundTruth) -> dict:
    """Score the anchors and check that the judges graded them completely.

    An anchor is invalid if its mean score is out of range, if any judge's
    verdict set for it is incomplete or flagged, or if a judge did not call a
    majority of the weight in the negation anchor contradicted.
    """
    if not anchors:
        return dict.fromkeys(ANCHOR_KINDS) | {
            "by_judge": {},
            "valid": None,
            "problems": [],
        }
    by_judge = {
        kind: _scores(anchors[f"anchor-{kind}"], truth) for kind in ANCHOR_KINDS
    }
    scores = {
        kind: _mean(list(per_judge.values())) for kind, per_judge in by_judge.items()
    }
    problems = anchor_problems(scores)
    for kind in ANCHOR_KINDS:
        problems.extend(_verdict_set_problems(kind, anchors[f"anchor-{kind}"]))
    problems.extend(_negation_problems(anchors["anchor-negation"], truth))
    return {**scores, "by_judge": by_judge, "valid": not problems, "problems": problems}


def _verdict_set_problems(kind: str, judged: Judged) -> list[str]:
    problems = []
    for judge, verdicts in judged.verdicts.items():
        flagged = [i for i, verdict in verdicts.items() if verdict.flags]
        if flagged:
            problems.append(
                f"{kind} anchor, judge {judge}: the verdicts for {', '.join(flagged)} "
                "are flagged (missing, invalid or ambiguous)"
            )
    problems.extend(f"{kind} anchor: {flag}" for flag in judged.flags)
    return problems


def _negation_problems(judged: Judged, truth: GroundTruth) -> list[str]:
    problems = []
    for judge, verdicts in judged.verdicts.items():
        if raw_credit(truth.references, verdicts) >= 0:
            # Contradictions must cost credit; a zero penalty passes every range.
            problems.append(
                f"negation anchor, judge {judge}: contradictions carry no penalty "
                "(raw credit is not negative)"
            )
        share = contradicted_weight_share(truth.references, verdicts)
        if share <= ANCHOR_NEGATION_CONTRADICTED_MIN:
            problems.append(
                f"negation anchor, judge {judge}: {share:.0%} of the assertion weight "
                "is judged contradicted, expected a majority"
            )
    return problems


def _setup_rows(rows: Sequence[dict], *, provisional: bool) -> list[dict]:
    scores: dict[str, list[float]] = {}
    for row in rows:
        scores.setdefault(row["setup"], []).append(row["score"])
    return [
        {
            "setup": setup,
            "n": len(values),
            "mean": _mean(values),
            "min": min(values),
            "max": max(values),
            "provisional": provisional,
        }
        for setup, values in scores.items()
    ]


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 2)


if __name__ == "__main__":
    raise SystemExit(main())
