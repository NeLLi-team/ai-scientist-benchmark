"""Tests for the assertion-level scorer and its judge adapters.

Judges run as stub `claude` and `codex` executables (tests/fixtures/stub_judge.py)
placed first on PATH. No test calls a model.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "benchmark-scoring" / "scripts"
STUB_JUDGE = Path(__file__).resolve().parent / "fixtures" / "stub_judge.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import assertion_grading  # noqa: E402  # needs the scripts dir on sys.path first
import assertion_judges  # noqa: E402  # same reason
import review_ground_truth  # noqa: E402  # same reason
import score_assertions  # noqa: E402  # same reason
import score_csag_candidates  # noqa: E402  # needs the scripts dir on sys.path first
from assertion_grading import Reference, Verdict  # noqa: E402  # same reason

REFERENCE_ROWS = (
    ("A0001", "Alpha exceeds beta in size.", "core"),
    ("A0002", "Gamma binds delta.", "major"),
    ("A0003", "Epsilon is conserved.", "supporting"),
    ("A0004", "Zeta is common.", "background"),
)
TEXTS = {label: text for label, text, _ in REFERENCE_ROWS}
REFERENCES = tuple(
    Reference(
        assertion_id=label,
        text=text,
        criticality=criticality,
        falsification_criteria=(f"{label} fails if sizes are equal.",),
        evidence_texts=(f"Measured result for {label}.",),
    )
    for label, text, criticality in REFERENCE_ROWS
)
MARKER = "SETUPMARKER"
SECRET_ENV = {"SECRET_API_TOKEN": "SENTINEL-ENV-TOKEN-1234567890"}
AUTH_CONTENT = "SENTINEL-AUTH-FILE-CONTENT-0987654321"
HOST_TOKENS = {"access_token": AUTH_CONTENT, "refresh_token": "R0"}
HOST_LOGIN = {"auth_mode": "chatgpt", "tokens": HOST_TOKENS, "last_refresh": "t0"}
NEGATION_MARK = "It is not true that"
CLAUDE_FLAGS = [
    "-p",
    "--model",
    "sonnet",
    "--effort",
    "xhigh",
    "--safe-mode",
    "--tools",
    "",
    "--no-session-persistence",
    "--strict-mcp-config",
    "--setting-sources",
    "",
    "--output-format",
    "json",
    "--json-schema",
]
CODEX_FLAGS = [
    "exec",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "-m",
    "gpt-6-astra",
    "-c",
    "model_reasoning_effort=xhigh",
    "--disable",
    "shell_tool",
    "--json",
]
VERDICT_FIELDS = {"assertion_id", "verdict", "candidate_ids", "quote", "reason"}


def sha256_of(path: Path) -> str:
    """SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ground_truth_csag() -> dict:
    """A four-assertion ground truth in the CSAG shape, one evidence item each."""
    assertions, items, links = [], [], []
    for label, text, criticality in REFERENCE_ROWS:
        assertion_id = f"csag:doc/gt/assertion/{label}"
        item_id = f"csag:doc/gt/evidence/{label}"
        assertions.append(
            {
                "id": assertion_id,
                "label": label,
                "assertion_text": text,
                "criticality": criticality,
                "claim_role": "result_claim",
                "falsification_criteria": [f"{label} fails if sizes are equal."],
            }
        )
        items.append(
            {
                "id": item_id,
                "label": label,
                "evidence_text": f"Measured result for {label}.",
            }
        )
        links.append(
            {
                "id": f"csag:doc/gt/elink/{label}",
                "evidence_item": item_id,
                "assertion": assertion_id,
                "polarity": "supports",
                "strength": "strong",
            }
        )
    return {
        "id": "csag:doc/gt",
        "title": "Ground truth",
        "assertions": assertions,
        "evidence_items": items,
        "evidence_links": links,
    }


def candidate_csag(texts: list[str], *, marker: str = "") -> dict:
    """A candidate CSAG whose assertions say `texts`, each with one evidence item."""
    assertions, items, links = [], [], []
    for index, text in enumerate(texts, start=1):
        assertion_id = f"csag:doc/{marker}/assertion/A{index:04d}"
        item_id = f"csag:doc/{marker}/evidence/E{index:04d}"
        assertions.append(
            {
                "id": assertion_id,
                "label": f"{marker} claim {index}",
                "assertion_text": text,
                "claim_role": "result_claim",
            }
        )
        items.append({"id": item_id, "evidence_text": f"Observed: {text}"})
        links.append(
            {
                "evidence_item": item_id,
                "assertion": assertion_id,
                "polarity": "supports",
                "strength": "strong",
            }
        )
    return {
        "id": f"csag:doc/{marker}",
        "title": f"{marker} title",
        "extraction_activities": [{"created_by": f"csag:agent/{marker}-model"}],
        "assertions": assertions,
        "evidence_items": items,
        "evidence_links": links,
    }


def write_json(path: Path, value: object) -> None:
    """Write `value` as JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def entries(**overrides: dict) -> list[dict]:
    """Judge entries for every reference: missing, unless `A000n` is overridden."""
    rows = []
    for label in TEXTS:
        row = {
            "assertion_id": label,
            "verdict": "missing",
            "candidate_ids": [],
            "quote": "",
            "reason": "r",
        }
        rows.append({**row, **overrides.get(label, {})})
    return rows


class CreditScoreTest(unittest.TestCase):
    def verdicts(self, *verdicts: str) -> dict[str, Verdict]:
        return {
            reference.assertion_id: Verdict(reference.assertion_id, verdict, (), "", "")
            for reference, verdict in zip(REFERENCES, verdicts, strict=True)
        }

    def test_credit_score_weights_matched_partial_missing_and_contradicted(
        self,
    ) -> None:
        verdicts = self.verdicts("matched", "partial", "missing", "contradicted")

        score = assertion_grading.credit_score(REFERENCES, verdicts)

        self.assertEqual(score, 45.0)

    def test_credit_score_is_100_when_everything_matches(self) -> None:
        verdicts = self.verdicts("matched", "matched", "matched", "matched")

        self.assertEqual(assertion_grading.credit_score(REFERENCES, verdicts), 100.0)

    def test_credit_score_floors_at_zero(self) -> None:
        verdicts = self.verdicts(*["contradicted"] * 4)

        self.assertEqual(assertion_grading.credit_score(REFERENCES, verdicts), 0.0)

    def test_contradicted_weight_share_counts_weight_not_assertions(self) -> None:
        verdicts = self.verdicts("matched", "contradicted", "contradicted", "missing")

        share = assertion_grading.contradicted_weight_share(REFERENCES, verdicts)

        self.assertEqual(share, 0.5)

    def test_agreement_is_the_share_of_assertions_with_one_verdict(self) -> None:
        first = self.verdicts("matched", "partial", "missing", "missing")
        second = self.verdicts("matched", "matched", "missing", "contradicted")

        agreement = assertion_grading.agreement({"a": first, "b": second})

        self.assertEqual(agreement, 0.5)

    def test_agreement_is_none_for_a_single_judge(self) -> None:
        only = self.verdicts("matched", "matched", "matched", "matched")

        self.assertIsNone(assertion_grading.agreement({"a": only}))


class PacketTest(unittest.TestCase):
    def test_packet_has_reference_fields_and_neutral_candidate_ids(self) -> None:
        candidate = candidate_csag([TEXTS["A0001"]], marker=MARKER)

        packet = assertion_grading.build_packet(REFERENCES, candidate)

        for expected in (
            "A0001",
            TEXTS["A0001"],
            "core",
            "A0001 fails if sizes are equal.",
            "Measured result for A0001.",
            "CA01",
            "CE01",
            "supports",
            "strong",
        ):
            self.assertIn(expected, packet.text)
        self.assertEqual(packet.candidate_ids, frozenset({"CA01", "CE01"}))

    def test_packet_leaves_out_candidate_identity(self) -> None:
        candidate = candidate_csag([TEXTS["A0001"]], marker=MARKER)

        packet = assertion_grading.build_packet(REFERENCES, candidate)

        self.assertNotIn(MARKER, packet.text)
        self.assertNotIn(MARKER, packet.candidate_text)


class ValidateVerdictsTest(unittest.TestCase):
    def setUp(self) -> None:
        candidate = candidate_csag([TEXTS["A0001"], TEXTS["A0002"]], marker="x")
        self.packet = assertion_grading.build_packet(REFERENCES, candidate)

    def validate(self, raw: list[dict]) -> tuple[dict[str, Verdict], list[str]]:
        return assertion_grading.validate_verdicts(raw, REFERENCES, self.packet)

    def test_validate_accepts_a_quote_with_different_whitespace(self) -> None:
        raw = entries(
            A0001={
                "verdict": "matched",
                "candidate_ids": ["CA01"],
                "quote": "Alpha   exceeds\nbeta  in size.",
            }
        )

        verdicts, flags = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "matched")
        self.assertEqual(verdicts["A0001"].flags, ())
        self.assertEqual(flags, [])

    def test_validate_turns_an_absent_quote_into_missing_with_a_flag(self) -> None:
        raw = entries(
            A0001={"verdict": "matched", "candidate_ids": ["CA01"], "quote": "nope"}
        )

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertIn("quote", verdicts["A0001"].flags[0])

    def test_validate_requires_a_quote_for_every_verdict_but_missing(self) -> None:
        raw = entries(A0001={"verdict": "partial", "candidate_ids": ["CA01"]})

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertTrue(verdicts["A0001"].flags)

    def test_validate_marks_a_reference_without_an_entry_as_missing(self) -> None:
        raw = [row for row in entries() if row["assertion_id"] != "A0003"]

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0003"].verdict, "missing")
        self.assertIn("no verdict", verdicts["A0003"].flags[0])

    def test_validate_flags_an_entry_for_an_unknown_assertion_id(self) -> None:
        raw = [*entries(), {**entries()[0], "assertion_id": "A9999"}]

        verdicts, flags = self.validate(raw)

        self.assertEqual(set(verdicts), set(TEXTS))
        self.assertTrue(any("A9999" in flag for flag in flags))

    def test_validate_makes_two_verdicts_for_one_assertion_missing_in_any_order(
        self,
    ) -> None:
        matched = {
            "verdict": "matched",
            "candidate_ids": ["CA01"],
            "quote": TEXTS["A0001"],
        }
        contradicted = {**matched, "verdict": "contradicted"}
        first_matched = [*entries(A0001=matched)[:1], *entries(A0001=contradicted)]
        first_contradicted = [*entries(A0001=contradicted)[:1], *entries(A0001=matched)]
        identical = [*entries(A0001=matched)[:1], *entries(A0001=matched)]

        for raw in (first_matched, first_contradicted, identical):
            verdicts, _ = self.validate(raw)

            self.assertEqual(verdicts["A0001"].verdict, "missing")
            self.assertIn("2 verdicts", verdicts["A0001"].flags[0])

    def test_validate_downgrades_an_entry_without_required_fields(self) -> None:
        bare = {"assertion_id": "A0001", "verdict": "matched", "quote": TEXTS["A0001"]}

        verdicts, _ = self.validate([bare, *entries()[1:]])

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertIn("candidate_ids", verdicts["A0001"].flags[0])
        self.assertIn("reason", verdicts["A0001"].flags[0])

    def test_validate_downgrades_a_verdict_citing_only_unknown_candidate_ids(
        self,
    ) -> None:
        raw = entries(
            A0001={
                "verdict": "matched",
                "candidate_ids": ["CA99"],
                "quote": TEXTS["A0001"],
            }
        )

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertIn("CA99", verdicts["A0001"].flags[0])

    def test_validate_keeps_a_verdict_and_drops_an_unknown_id_beside_a_known_one(
        self,
    ) -> None:
        raw = entries(
            A0001={
                "verdict": "matched",
                "candidate_ids": ["CA01", "CA99"],
                "quote": TEXTS["A0001"],
            }
        )

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "matched")
        self.assertEqual(verdicts["A0001"].candidate_ids, ("CA01",))
        self.assertIn("CA99", verdicts["A0001"].flags[0])

    def test_validate_downgrades_a_verdict_that_cites_no_candidate_id(self) -> None:
        raw = entries(A0001={"verdict": "matched", "quote": TEXTS["A0001"]})

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertIn("candidate id", verdicts["A0001"].flags[0])

    def test_validate_survives_null_and_mistyped_fields(self) -> None:
        nulls = {
            "assertion_id": "A0001",
            "verdict": None,
            "candidate_ids": None,
            "quote": None,
            "reason": None,
        }
        no_id = {**nulls, "assertion_id": None}
        list_id = {**nulls, "assertion_id": ["A0001"]}
        text_ids = {**entries()[1], "candidate_ids": "CA01"}

        verdicts, flags = self.validate([no_id, list_id, nulls, text_ids])

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertEqual(verdicts["A0002"].verdict, "missing")
        self.assertTrue(verdicts["A0001"].flags)
        self.assertEqual(len(flags), 2)

    def test_validate_treats_an_unknown_verdict_label_as_missing(self) -> None:
        raw = entries(A0001={"verdict": "maybe"})

        verdicts, _ = self.validate(raw)

        self.assertEqual(verdicts["A0001"].verdict, "missing")
        self.assertIn("maybe", verdicts["A0001"].flags[0])


class ScorerRunTestCase(unittest.TestCase):
    """Builds a case, candidates and stub judges in a temp tree and runs main()."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.case = self.root / "ground_truth" / "demo"
        self.candidates = self.root / "candidates"
        self.out = self.root / "out"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.bin.mkdir()
        (self.home / ".codex").mkdir(parents=True)
        self.host_login = self.home / ".codex" / "auth.json"
        self.host_login.write_text(json.dumps(HOST_LOGIN), encoding="utf-8")
        self.host_login.chmod(0o600)
        for name in ("claude", "codex"):
            (self.bin / name).symlink_to(STUB_JUDGE)
        self.truth = self.case / "csag" / "paper_extraction.json"
        write_json(self.truth, ground_truth_csag())
        write_json(self.case / "scoring" / "scoring_schema.json", {})
        write_json(
            self.root / "unrelated.json",
            candidate_csag(["Something else."], marker="u"),
        )

    def sign(self, edits: dict | None = None, *, sha: str | None = None) -> None:
        """Write signoff.json the way the review script does."""
        assertions = {
            label: {"decision": "approve", "criticality": criticality, "note": ""}
            for label, _, criticality in REFERENCE_ROWS
        }
        for label, change in (edits or {}).items():
            assertions[label] = {**assertions[label], **change}
        write_json(
            self.case / "csag" / "signoff.json",
            {
                "case_id": "demo",
                "ground_truth_sha256": sha or sha256_of(self.truth),
                "reviewer": "TESTER",
                "signed_at": "2026-10-19T00:00:00Z",
                "assertions": assertions,
            },
        )

    def add_candidate(self, name: str, texts: list[str], *, marker: str = "") -> None:
        write_json(
            self.candidates / f"{name}_csag.json", candidate_csag(texts, marker=marker)
        )

    def configure_stub(self, config: dict) -> None:
        write_json(self.bin / "stub_config.json", config)

    def calls(self) -> list[dict]:
        log = self.bin / "calls.jsonl"
        if not log.exists():
            return []
        lines = log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def run_scorer(self, *extra: str) -> tuple[int, str]:
        """Run main() with stub judges; return the exit code and stderr+stdout."""
        argv = [
            "--case-dir",
            str(self.case),
            "--candidate-dir",
            str(self.candidates),
            "--out-dir",
            str(self.out),
            *extra,
        ]
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env = {"PATH": path, "HOME": str(self.home), **SECRET_ENV}
        stream = io.StringIO()
        with (
            mock.patch.dict(os.environ, env),
            contextlib.redirect_stderr(stream),
            contextlib.redirect_stdout(stream),
        ):
            # A developer's own login location must never reach a test run.
            os.environ.pop("CODEX_HOME", None)
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            code = score_assertions.main(argv)
        return code, stream.getvalue()

    def results(self) -> dict:
        return json.loads((self.out / "results.json").read_text(encoding="utf-8"))

    def candidate_result(self, candidate_id: str) -> dict:
        found = [c for c in self.results()["candidates"] if c["id"] == candidate_id]
        return found[0]


class SignoffGateTest(ScorerRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_candidate("solo", [TEXTS["A0002"]], marker="m")

    def test_require_signoff_refuses_without_signoff(self) -> None:
        code, output = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 2)
        self.assertIn("signoff.json", output)
        self.assertEqual(self.calls(), [])

    def test_require_signoff_refuses_a_signoff_for_another_ground_truth(self) -> None:
        self.sign(sha="0" * 64)

        code, output = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 2)
        self.assertIn("sha256", output)
        self.assertEqual(self.calls(), [])

    def test_require_signoff_refuses_a_signoff_that_skips_an_assertion(self) -> None:
        self.sign()
        sheet = json.loads((self.case / "csag" / "signoff.json").read_text("utf-8"))
        del sheet["assertions"]["A0003"]
        write_json(self.case / "csag" / "signoff.json", sheet)

        code, output = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 2)
        self.assertIn("A0003", output)

    def edit_signoff(self, change) -> None:
        """Sign the ground truth, then let `change` alter the parsed signoff.json."""
        self.sign()
        path = self.case / "csag" / "signoff.json"
        sheet = json.loads(path.read_text(encoding="utf-8"))
        change(sheet)
        write_json(path, sheet)

    def assert_refused(self, change, expected: str) -> None:
        self.edit_signoff(change)

        code, output = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 2)
        self.assertIn(expected, output)
        self.assertEqual(self.calls(), [])

    def test_require_signoff_refuses_a_signoff_without_a_reviewer(self) -> None:
        self.assert_refused(lambda sheet: sheet.pop("reviewer"), "reviewer")

    def test_require_signoff_refuses_a_signoff_with_a_blank_reviewer(self) -> None:
        self.assert_refused(lambda sheet: sheet.update(reviewer=" "), "reviewer")

    def test_require_signoff_refuses_a_signoff_without_a_signing_time(self) -> None:
        self.assert_refused(lambda sheet: sheet.pop("signed_at"), "signed_at")

    def test_require_signoff_refuses_a_signoff_with_an_unreadable_signing_time(
        self,
    ) -> None:
        self.assert_refused(lambda sheet: sheet.update(signed_at="soon"), "signed_at")

    def test_require_signoff_refuses_a_signoff_made_for_another_case(self) -> None:
        self.assert_refused(lambda sheet: sheet.update(case_id="other"), "case_id")

    def test_require_signoff_refuses_an_approve_that_changes_the_criticality(
        self,
    ) -> None:
        def change(sheet) -> None:
            sheet["assertions"]["A0001"]["criticality"] = "background"

        self.assert_refused(change, "approve")

    def test_require_signoff_refuses_an_unknown_decision(self) -> None:
        def change(sheet) -> None:
            sheet["assertions"]["A0001"]["decision"] = "maybe"

        self.assert_refused(change, "maybe")

    def test_require_signoff_refuses_an_unknown_criticality(self) -> None:
        def change(sheet) -> None:
            sheet["assertions"]["A0002"] = {"decision": "edit", "criticality": "huge"}

        self.assert_refused(change, "huge")

    def test_require_signoff_refuses_a_decision_for_an_assertion_the_truth_lacks(
        self,
    ) -> None:
        def change(sheet) -> None:
            sheet["assertions"]["A9999"] = sheet["assertions"]["A0001"]

        self.assert_refused(change, "A9999")

    def test_require_signoff_refuses_a_signoff_that_is_not_json(self) -> None:
        (self.case / "csag").mkdir(parents=True, exist_ok=True)
        (self.case / "csag" / "signoff.json").write_text("not json", "utf-8")

        code, output = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 2)
        self.assertIn("signoff.json", output)

    def test_an_incomplete_signoff_scores_provisionally_by_default(self) -> None:
        def change(sheet) -> None:
            del sheet["assertions"]["A0003"]
            sheet["assertions"]["A0004"]["decision"] = "reject"

        self.edit_signoff(change)

        code, output = self.run_scorer("--anchors", "none")

        results = self.results()
        self.assertEqual(code, 0)
        self.assertTrue(results["provisional"])
        self.assertFalse(results["signed"])
        self.assertIn("A0003", results["signoff_problem"])
        self.assertIn("A0003", output)
        self.assertIn("A0004", self.candidate_result("solo")["verdicts"]["sonnet"])

    def test_a_signoff_that_fails_validation_scores_provisionally_by_default(
        self,
    ) -> None:
        def change(sheet) -> None:
            sheet["assertions"]["A0001"]["criticality"] = "background"

        self.edit_signoff(change)

        code, _ = self.run_scorer("--anchors", "none")

        self.assertEqual(code, 0)
        self.assertTrue(self.results()["provisional"])
        self.assertIn("approve", self.results()["signoff_problem"])

    def test_a_signoff_that_is_not_json_scores_provisionally_by_default(self) -> None:
        (self.case / "csag").mkdir(parents=True, exist_ok=True)
        (self.case / "csag" / "signoff.json").write_text("not json", "utf-8")

        code, _ = self.run_scorer("--anchors", "none")

        self.assertEqual(code, 0)
        self.assertIn("signoff.json", self.results()["signoff_problem"])

    def test_a_valid_signoff_leaves_no_signoff_problem(self) -> None:
        self.sign()

        self.run_scorer("--anchors", "none")

        self.assertIsNone(self.results()["signoff_problem"])

    def test_run_accepts_a_signoff_written_by_the_review_tool(self) -> None:
        sheet = self.root / "sheet.md"
        review_ground_truth.write_packet(self.case, sheet)
        text = sheet.read_text(encoding="utf-8")
        edit_a0002 = re.sub(
            r"(?ms)(^## A0002$.*?^)decision:$(.*?^)criticality: major$",
            r"\1decision: edit\2criticality: core",
            text,
        )
        edited = re.sub(r"(?m)^decision:$", "decision: approve", edit_a0002)
        sheet.write_text(edited, encoding="utf-8")
        review_ground_truth.sign_off(self.case, sheet, reviewer="TESTER")

        code, _ = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 0)
        self.assertTrue(self.results()["signed"])
        self.assertEqual(self.candidate_result("solo")["score"], 36.36)

    def test_a_missing_signoff_scores_provisionally_with_a_warning(self) -> None:
        code, output = self.run_scorer("--anchors", "none")

        results = self.results()
        self.assertEqual(code, 0)
        self.assertIn("no signoff.json", output)
        self.assertIn("provisional", output)
        self.assertFalse(results["signed"])
        self.assertTrue(results["provisional"])
        self.assertTrue(self.candidate_result("solo")["provisional"])
        self.assertTrue(results["setups"][0]["provisional"])
        self.assertIn("no signoff.json", results["signoff_problem"])

    def test_a_stale_signoff_is_ignored_and_the_run_is_provisional(self) -> None:
        self.sign({"A0004": {"decision": "reject"}}, sha="0" * 64)

        code, _ = self.run_scorer("--anchors", "none")

        self.assertEqual(code, 0)
        self.assertTrue(self.results()["provisional"])
        self.assertIn("A0004", self.candidate_result("solo")["verdicts"]["sonnet"])

    def test_require_signoff_runs_with_a_valid_signoff(self) -> None:
        self.sign({"A0004": {"decision": "reject"}})

        code, _ = self.run_scorer("--anchors", "none", "--require-signoff")

        self.assertEqual(code, 0)
        self.assertTrue(self.results()["signed"])
        self.assertFalse(self.results()["provisional"])
        self.assertNotIn("A0004", self.candidate_result("solo")["verdicts"]["sonnet"])

    def test_the_allow_unsigned_flag_is_gone(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_scorer("--anchors", "none", "--allow-unsigned")

        self.assertEqual(raised.exception.code, 2)

    def test_signed_run_applies_edits_and_drops_rejected_assertions(self) -> None:
        self.sign(
            {
                "A0002": {"decision": "edit", "criticality": "core", "note": "x"},
                "A0004": {"decision": "reject"},
            }
        )

        code, _ = self.run_scorer("--anchors", "none")

        solo = self.candidate_result("solo")
        self.assertEqual(code, 0)
        self.assertFalse(self.results()["provisional"])
        self.assertTrue(self.results()["signed"])
        self.assertEqual(solo["score"], 40.0)
        self.assertNotIn("A0004", solo["verdicts"]["sonnet"])
        self.assertNotIn("Zeta is common.", self.calls()[0]["packet"])


class GroundTruthReadTest(ScorerRunTestCase):
    def test_ground_truth_is_hashed_and_parsed_from_one_read(self) -> None:
        self.sign()
        changed = ground_truth_csag()
        changed["assertions"][0]["assertion_text"] = "Swapped after the hash."
        real_read_bytes = Path.read_bytes

        def read_then_swap(path: Path) -> bytes:
            data = real_read_bytes(path)
            if path == self.truth:
                write_json(self.truth, changed)
            return data

        with mock.patch.object(Path, "read_bytes", read_then_swap):
            truth = score_assertions.load_ground_truth(self.case, require_signoff=True)

        self.assertEqual(truth.references[0].text, TEXTS["A0001"])
        self.assertTrue(truth.signed)


class ScorerEndToEndTest(ScorerRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sign()
        full = [TEXTS["A0001"], TEXTS["A0002"], TEXTS["A0003"]]
        self.add_candidate("alpha__r01", full, marker=MARKER)
        self.add_candidate("alpha__r02", [TEXTS["A0001"]], marker=MARKER)
        self.add_candidate("beta", ["Unrelated claim."], marker="b")
        self.anchor_args = ("--unrelated-csag", str(self.root / "unrelated.json"))

    def test_run_scores_candidates_by_the_weighted_credit_formula(self) -> None:
        code, _ = self.run_scorer(*self.anchor_args)

        self.assertEqual(code, 0)
        full = self.candidate_result("alpha__r01")
        self.assertEqual(full["score"], 90.0)
        self.assertEqual(full["score_by_judge"], {"sonnet": 90.0, "astra": 90.0})
        self.assertEqual(full["agreement"], 1.0)
        self.assertEqual(full["core_coverage"], "1/1")
        self.assertEqual(full["contradicted"], 0)
        self.assertEqual(full["verdicts"]["sonnet"]["A0004"]["verdict"], "missing")
        self.assertEqual(self.candidate_result("beta")["core_coverage"], "0/1")

    def test_run_groups_repeats_by_setup(self) -> None:
        self.run_scorer(*self.anchor_args)

        setups = {item["setup"]: item for item in self.results()["setups"]}
        self.assertEqual(setups["alpha"]["n"], 2)
        self.assertEqual(setups["alpha"]["mean"], 65.0)
        self.assertEqual(setups["alpha"]["min"], 40.0)
        self.assertEqual(setups["alpha"]["max"], 90.0)
        self.assertEqual(setups["beta"]["n"], 1)
        self.assertEqual(self.candidate_result("alpha__r02")["setup"], "alpha")
        self.assertEqual(self.candidate_result("alpha__r02")["run"], "r02")
        self.assertIsNone(self.candidate_result("beta")["run"])

    def test_run_records_judge_versions_and_models(self) -> None:
        self.run_scorer(*self.anchor_args)

        judges = {item["name"]: item for item in self.results()["judges"]}
        self.assertEqual(judges["sonnet"]["cli_version"], "9.9.9-stub (Claude Code)")
        self.assertEqual(judges["sonnet"]["model"], "claude-stub-sonnet")
        self.assertEqual(judges["astra"]["cli_version"], "codex-cli 0.0.1-stub")
        self.assertEqual(judges["astra"]["model"], "gpt-stub-astra")

    def test_run_stores_one_judgment_record_per_candidate_and_judge(self) -> None:
        self.run_scorer(*self.anchor_args)

        record = json.loads(
            (self.out / "judgments" / "alpha__r01.sonnet.json").read_text("utf-8")
        )
        self.assertEqual(record["usage"]["input_tokens"], 100)
        self.assertEqual(record["model"], "claude-stub-sonnet")
        self.assertEqual(record["cli_version"], "9.9.9-stub (Claude Code)")
        self.assertIn("verdicts", record["raw_output"])
        self.assertEqual(len(record["packet_sha256"]), 64)
        codex_record = json.loads(
            (self.out / "judgments" / "alpha__r01.astra.json").read_text("utf-8")
        )
        self.assertEqual(codex_record["usage"]["input_tokens"], 100)
        self.assertEqual(codex_record["model"], "gpt-stub-astra")
        self.assertEqual(len(list((self.out / "judgments").glob("*.json"))), 12)

    def test_run_resumes_without_repeating_finished_calls(self) -> None:
        self.run_scorer(*self.anchor_args)
        first_calls = len(self.calls())

        code, _ = self.run_scorer(*self.anchor_args)

        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls()), first_calls)
        self.assertEqual(self.candidate_result("alpha__r01")["score"], 90.0)

    def test_run_rejudges_a_candidate_whose_packet_changed(self) -> None:
        self.run_scorer(*self.anchor_args)
        first_calls = len(self.calls())
        self.add_candidate("beta", [TEXTS["A0004"]], marker="b")

        self.run_scorer(*self.anchor_args)

        self.assertEqual(len(self.calls()), first_calls + 2)
        self.assertEqual(self.candidate_result("beta")["score"], 10.0)

    def test_anchors_pass_when_judges_behave(self) -> None:
        self.run_scorer(*self.anchor_args)

        anchors = self.results()["anchors"]
        self.assertEqual(anchors["source"], 100.0)
        self.assertEqual(anchors["unrelated"], 0.0)
        self.assertEqual(anchors["negation"], 0.0)
        self.assertTrue(anchors["valid"])
        self.assertEqual(anchors["problems"], [])
        self.assertEqual(len(self.calls()), 12)

    def test_anchors_are_invalid_when_the_unrelated_paper_matches(self) -> None:
        write_json(self.root / "unrelated.json", ground_truth_csag())

        self.run_scorer(*self.anchor_args)

        anchors = self.results()["anchors"]
        self.assertFalse(anchors["valid"])
        self.assertEqual(len(anchors["problems"]), 1)
        self.assertIn("unrelated", anchors["problems"][0])

    def test_anchors_are_invalid_when_a_judge_returns_no_verdicts_for_the_negation(
        self,
    ) -> None:
        self.configure_stub({"claude": {"empty": True, "when": NEGATION_MARK}})

        self.run_scorer(*self.anchor_args)

        anchors = self.results()["anchors"]
        self.assertFalse(anchors["valid"])
        self.assertTrue(
            any("negation" in p and "sonnet" in p for p in anchors["problems"])
        )

    def test_anchors_are_invalid_when_the_negation_is_graded_missing(self) -> None:
        missing = dict.fromkeys(TEXTS, "missing")
        self.configure_stub({"codex": {"force": missing, "when": NEGATION_MARK}})

        self.run_scorer(*self.anchor_args)

        anchors = self.results()["anchors"]
        self.assertEqual(anchors["negation"], 0.0)
        self.assertFalse(anchors["valid"])
        self.assertTrue(
            any("contradicted" in p and "astra" in p for p in anchors["problems"])
        )

    def test_anchors_are_invalid_when_contradictions_cost_nothing(self) -> None:
        no_penalty = {**assertion_grading.CREDIT, "contradicted": 0.0}

        with mock.patch.dict(assertion_grading.CREDIT, no_penalty):
            self.run_scorer(*self.anchor_args)

        anchors = self.results()["anchors"]
        self.assertFalse(anchors["valid"])
        self.assertTrue(any("penalty" in p for p in anchors["problems"]))

    def test_anchors_are_invalid_when_an_anchor_verdict_is_flagged(self) -> None:
        self.configure_stub({"claude": {"bad_quote": True, "when": "Measured result"}})

        self.run_scorer(*self.anchor_args)

        anchors = self.results()["anchors"]
        self.assertFalse(anchors["valid"])
        self.assertTrue(
            any("source" in p and "flagged" in p for p in anchors["problems"])
        )

    def test_a_judge_answer_that_is_not_json_is_not_cached_for_reuse(self) -> None:
        self.configure_stub({"claude": {"garbage": True}})
        first_code, _ = self.run_scorer("--anchors", "none", "--judges", "sonnet")
        judgments = self.out / "judgments"
        self.assertEqual(first_code, 1)
        self.assertFalse((judgments / "alpha__r01.sonnet.json").exists())
        self.assertTrue((judgments / "alpha__r01.sonnet.invalid-1.json").exists())
        self.configure_stub({})

        code, _ = self.run_scorer("--anchors", "none", "--judges", "sonnet")

        self.assertEqual(code, 0)
        self.assertEqual(self.candidate_result("alpha__r01")["score"], 90.0)
        self.assertEqual(len(self.calls()), 4)

    def test_a_cached_invalid_answer_is_renamed_and_requested_again(self) -> None:
        self.run_scorer("--anchors", "none", "--judges", "sonnet")
        first_calls = len(self.calls())
        record_path = self.out / "judgments" / "beta.sonnet.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["raw_output"] = "not json"
        write_json(record_path, record)

        code, _ = self.run_scorer("--anchors", "none", "--judges", "sonnet")

        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls()), first_calls + 1)
        kept = self.out / "judgments" / "beta.sonnet.invalid-1.json"
        self.assertEqual(json.loads(kept.read_text("utf-8"))["raw_output"], "not json")
        fresh = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertIn("verdicts", fresh["raw_output"])

    def test_cached_only_reuses_stored_judgments_without_a_call(self) -> None:
        self.run_scorer(*self.anchor_args)
        first_calls = len(self.calls())

        code, _ = self.run_scorer(*self.anchor_args, "--cached-only")

        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls()), first_calls)
        self.assertEqual(self.candidate_result("alpha__r01")["score"], 90.0)

    def test_cached_only_stops_when_a_judgment_is_not_stored(self) -> None:
        self.run_scorer("--anchors", "none", "--judges", "sonnet")
        first_calls = len(self.calls())

        code, output = self.run_scorer("--anchors", "none", "--cached-only")

        self.assertEqual(code, 1)
        self.assertIn("--cached-only", output)
        self.assertEqual(len(self.calls()), first_calls)

    def test_anchors_none_skips_the_anchor_calls(self) -> None:
        self.run_scorer("--anchors", "none")

        anchors = self.results()["anchors"]
        self.assertIsNone(anchors["valid"])
        self.assertEqual(len(self.calls()), 6)

    def test_run_requires_the_default_unrelated_csag_when_anchors_are_on(self) -> None:
        code, output = self.run_scorer()

        self.assertEqual(code, 2)
        self.assertIn("testpdf", output)

    def test_run_reports_the_keyword_score_beside_the_new_score(self) -> None:
        with mock.patch.object(
            score_csag_candidates, "score_candidate", return_value={"total_score": 42.5}
        ) as keyword:
            self.run_scorer("--anchors", "none")

        self.assertEqual(self.candidate_result("beta")["keyword_score"], 42.5)
        self.assertEqual(keyword.call_count, 3)

    def test_run_writes_a_ranked_markdown_report_with_a_verdict_matrix(self) -> None:
        self.run_scorer(*self.anchor_args)

        report = (self.out / "results.md").read_text(encoding="utf-8")
        self.assertIn("alpha__r01", report)
        self.assertIn("source", report)
        self.assertIn("A0003", report)
        self.assertNotIn("PROVISIONAL", report)
        self.assertLess(report.index("alpha__r01"), report.index("beta"))

    def test_run_marks_the_markdown_report_provisional_when_unsigned(self) -> None:
        (self.case / "csag" / "signoff.json").unlink()

        self.run_scorer("--anchors", "none")

        report = (self.out / "results.md").read_text(encoding="utf-8")
        self.assertIn("PROVISIONAL", report)
        self.assertIn("no signoff.json", report)

    def test_judges_option_selects_the_judges_to_call(self) -> None:
        code, _ = self.run_scorer("--anchors", "none", "--judges", "sonnet")

        self.assertEqual(code, 0)
        self.assertEqual({call["agent"] for call in self.calls()}, {"claude"})
        self.assertEqual(
            list(self.candidate_result("beta")["score_by_judge"]), ["sonnet"]
        )
        self.assertIsNone(self.candidate_result("beta")["agreement"])

    def test_a_missing_codex_login_stops_the_run_with_a_clear_message(self) -> None:
        (self.home / ".codex" / "auth.json").unlink()

        code, output = self.run_scorer("--anchors", "none", "--judges", "astra")

        self.assertEqual(code, 1)
        self.assertIn("codex login", output)
        self.assertEqual(self.calls(), [])

    def test_unknown_judge_is_refused(self) -> None:
        code, output = self.run_scorer("--judges", "sonnet,gpt9")

        self.assertEqual(code, 2)
        self.assertIn("gpt9", output)

    def test_judges_disagreeing_lowers_agreement_and_averages_the_scores(self) -> None:
        self.configure_stub({"codex": {"force": {"A0001": "partial"}}})

        self.run_scorer("--anchors", "none")

        full = self.candidate_result("alpha__r01")
        self.assertEqual(full["score_by_judge"], {"sonnet": 90.0, "astra": 70.0})
        self.assertEqual(full["score"], 80.0)
        self.assertEqual(full["agreement"], 0.75)

    def test_a_contradiction_is_counted_for_a_single_judge(self) -> None:
        self.configure_stub({"claude": {"force": {"A0002": "contradicted"}}})

        self.run_scorer("--anchors", "none", "--judges", "sonnet")

        self.assertEqual(self.candidate_result("alpha__r01")["contradicted"], 1)

    def test_contradicted_counts_assertions_that_any_judge_contradicted(self) -> None:
        self.configure_stub(
            {
                "claude": {"force": {"A0002": "contradicted"}},
                "codex": {"force": {"A0002": "contradicted", "A0003": "contradicted"}},
            }
        )

        self.run_scorer("--anchors", "none")

        full = self.candidate_result("alpha__r01")
        self.assertEqual(full["contradicted"], 2)
        self.assertIsInstance(full["contradicted"], int)

    def test_judge_models_come_from_this_run_not_stale_records(self) -> None:
        stale = self.out / "judgments" / "gone.sonnet.json"
        write_json(stale, {"model": "stale-model"})

        self.run_scorer("--anchors", "none", "--judges", "sonnet")

        self.assertEqual(self.results()["judges"][0]["model"], "claude-stub-sonnet")

    def test_flags_from_bad_judge_output_reach_the_results(self) -> None:
        self.configure_stub({"claude": {"bad_quote": True, "unknown_id": True}})

        self.run_scorer("--anchors", "none", "--judges", "sonnet")

        full = self.candidate_result("alpha__r01")
        self.assertEqual(full["score"], 0.0)
        self.assertEqual(full["verdicts"]["sonnet"]["A0001"]["verdict"], "missing")
        self.assertTrue(full["verdicts"]["sonnet"]["A0001"]["flags"])
        self.assertTrue(any("A9999" in flag for flag in full["flags"]))

    def test_a_failing_judge_stops_the_run_and_keeps_finished_calls(self) -> None:
        self.configure_stub({"codex": {"fail": True}})

        code, output = self.run_scorer("--anchors", "none")

        self.assertEqual(code, 1)
        self.assertIn("astra", output)
        self.assertFalse((self.out / "results.json").exists())
        self.assertTrue((self.out / "judgments" / "alpha__r01.sonnet.json").exists())

    def test_a_judge_that_does_not_return_json_stops_the_run(self) -> None:
        self.configure_stub({"claude": {"garbage": True}})

        code, output = self.run_scorer("--anchors", "none", "--judges", "sonnet")

        self.assertEqual(code, 1)
        self.assertIn("sonnet", output)


class JudgeIsolationTest(ScorerRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sign()
        self.add_candidate(f"{MARKER}__r01", [TEXTS["A0001"]], marker=MARKER)
        self.first_code, self.first_output = self.run_scorer("--anchors", "none")
        self.claude = next(c for c in self.calls() if c["agent"] == "claude")
        self.codex = next(c for c in self.calls() if c["agent"] == "codex")

    def test_claude_command_line_has_no_tools_settings_or_mcp(self) -> None:
        argv = self.claude["argv"]

        self.assertEqual(argv[:-1], CLAUDE_FLAGS)
        schema = json.loads(argv[-1])
        verdict_item = schema["properties"]["verdicts"]["items"]
        self.assertEqual(set(verdict_item["required"]), VERDICT_FIELDS)
        self.assertEqual(
            set(verdict_item["properties"]["verdict"]["enum"]),
            {"matched", "partial", "missing", "contradicted"},
        )

    def test_codex_command_line_is_read_only_with_an_output_schema(self) -> None:
        argv = self.codex["argv"]

        self.assertEqual(argv[: len(CODEX_FLAGS)], CODEX_FLAGS)
        self.assertIn("--output-schema", argv)
        self.assertIn("-o", argv)

    def test_packets_hold_no_candidate_file_name_or_identity(self) -> None:
        for call in (self.claude, self.codex):
            self.assertNotIn(MARKER, call["packet"])
            self.assertNotIn(MARKER, json.dumps(call["argv"]))
            self.assertNotIn(MARKER, call["cwd"])

    def test_judges_run_in_a_fresh_empty_temp_directory_that_is_removed(self) -> None:
        for call in (self.claude, self.codex):
            cwd = Path(call["cwd"])
            self.assertEqual(call["cwd_entries"], [])
            self.assertNotIn(str(self.root / "candidates"), call["cwd"])
            self.assertFalse(cwd.exists())

    def test_judges_get_a_minimal_environment(self) -> None:
        allowed = {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TERM",
            "TMPDIR",
            "CODEX_HOME",
        }
        for call in (self.claude, self.codex):
            self.assertLessEqual(set(call["env"]), allowed)
            self.assertNotIn("SECRET_API_TOKEN", call["env"])
        self.assertEqual(self.claude["env"]["HOME"], str(self.home))

    def test_codex_gets_a_private_home_with_a_linked_login(self) -> None:
        self.assertNotEqual(self.codex["env"]["HOME"], str(self.home))
        self.assertTrue(
            self.codex["env"]["CODEX_HOME"].startswith(self.codex["env"]["HOME"])
        )
        self.assertTrue(self.codex["auth_is_symlink"])
        self.assertTrue(self.codex["auth_readable"])

    def test_login_contents_never_reach_output_or_records(self) -> None:
        written = [path for path in self.out.rglob("*") if path.is_file()]
        for text in [self.first_output, *(p.read_text("utf-8") for p in written)]:
            self.assertNotIn(AUTH_CONTENT, text)
            self.assertNotIn(SECRET_ENV["SECRET_API_TOKEN"], text)


class LoginRefreshTest(ScorerRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sign()
        self.add_candidate("solo", [TEXTS["A0001"]], marker="m")
        self.original = self.host_login.read_bytes()

    def host_tokens(self) -> dict:
        return json.loads(self.host_login.read_text(encoding="utf-8"))["tokens"]

    def test_a_refreshed_codex_login_is_written_back_to_the_host(self) -> None:
        self.configure_stub({"codex": {"refresh_auth": "valid"}})

        code, _ = self.run_scorer("--anchors", "none", "--judges", "astra")

        self.assertEqual(code, 0)
        self.assertEqual(self.host_tokens()["access_token"], "REFRESHED-ACCESS-TOKEN")
        self.assertEqual(self.host_login.stat().st_mode & 0o777, 0o600)

    def test_a_valid_refresh_is_kept_even_when_the_call_failed(self) -> None:
        self.configure_stub({"codex": {"refresh_auth": "valid", "fail": True}})

        code, _ = self.run_scorer("--anchors", "none", "--judges", "astra")

        self.assertEqual(code, 1)
        self.assertEqual(self.host_tokens()["access_token"], "REFRESHED-ACCESS-TOKEN")

    def test_a_failed_call_never_replaces_the_login_with_an_empty_file(self) -> None:
        self.configure_stub({"codex": {"refresh_auth": "empty", "fail": True}})

        code, _ = self.run_scorer("--anchors", "none", "--judges", "astra")

        self.assertEqual(code, 1)
        self.assertEqual(self.host_login.read_bytes(), self.original)

    def test_a_replacement_that_lost_login_keys_is_not_installed(self) -> None:
        self.configure_stub({"codex": {"refresh_auth": "missing_keys"}})

        self.run_scorer("--anchors", "none", "--judges", "astra")

        self.assertEqual(self.host_login.read_bytes(), self.original)


class InstallRefreshedLoginTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.host_dir = self.root / "host"
        self.host_dir.mkdir()
        self.host = self.host_dir / "auth.json"
        self.host.write_text(json.dumps(HOST_LOGIN), encoding="utf-8")
        self.host.chmod(0o600)
        self.count = 0

    def refreshed(self, token: str) -> str:
        tokens = {**HOST_TOKENS, "access_token": token}
        return json.dumps({**HOST_LOGIN, "tokens": tokens, "last_refresh": token})

    def make_login(self, content: str) -> Path:
        """A regular auth.json that a CLI left behind in its own private home."""
        self.count += 1
        directory = self.root / f"home{self.count}"
        directory.mkdir()
        login = directory / "auth.json"
        login.write_text(content, encoding="utf-8")
        return login

    def test_install_replaces_the_host_file_with_mode_0600_and_no_leftovers(
        self,
    ) -> None:
        login = self.make_login(self.refreshed("t1"))

        installed = assertion_judges.install_refreshed_login(login, self.host)

        self.assertTrue(installed)
        self.assertEqual(self.host.read_text("utf-8"), self.refreshed("t1"))
        self.assertEqual(self.host.stat().st_mode & 0o777, 0o600)
        names = {path.name for path in self.host_dir.iterdir()}
        self.assertEqual(names - {"auth.json.judge-lock"}, {"auth.json"})

    def test_install_refuses_a_replacement_older_than_the_host_login(self) -> None:
        self.host.write_text(self.refreshed("t5"), encoding="utf-8")
        login = self.make_login(self.refreshed("t3"))

        installed = assertion_judges.install_refreshed_login(login, self.host)

        self.assertFalse(installed)
        self.assertEqual(json.loads(self.host.read_text("utf-8"))["last_refresh"], "t5")

    def test_install_refuses_a_replacement_with_a_mistyped_token(self) -> None:
        for wrong in (7, True, ["x"], {"a": 1}):
            tokens = {**HOST_TOKENS, "refresh_token": wrong}
            login = self.make_login(
                json.dumps({**HOST_LOGIN, "tokens": tokens, "last_refresh": "t9"})
            )

            installed = assertion_judges.install_refreshed_login(login, self.host)

            self.assertFalse(installed, wrong)
        self.assertEqual(json.loads(self.host.read_text("utf-8")), HOST_LOGIN)

    def test_install_never_truncates_the_file_it_replaces(self) -> None:
        login = self.make_login(self.refreshed("t1"))
        with self.host.open(encoding="utf-8") as old_reader:
            assertion_judges.install_refreshed_login(login, self.host)

            self.assertEqual(json.loads(old_reader.read()), HOST_LOGIN)

    def test_install_ignores_a_login_that_is_still_a_symlink(self) -> None:
        link = self.root / "link" / "auth.json"
        link.parent.mkdir()
        link.symlink_to(self.host)
        before = self.host.read_bytes()

        installed = assertion_judges.install_refreshed_login(link, self.host)

        self.assertFalse(installed)
        self.assertEqual(self.host.read_bytes(), before)

    def test_install_refuses_empty_garbage_and_incomplete_replacements(self) -> None:
        before = self.host.read_bytes()
        incomplete = json.dumps({"auth_mode": "chatgpt", "tokens": {}})

        for content in ("", "not json", "[]", incomplete):
            login = self.make_login(content)

            installed = assertion_judges.install_refreshed_login(login, self.host)

            self.assertFalse(installed)
            self.assertEqual(self.host.read_bytes(), before)

    def test_install_skips_a_replacement_equal_to_the_host_file(self) -> None:
        login = self.make_login(self.host.read_text("utf-8"))
        inode = self.host.stat().st_ino

        installed = assertion_judges.install_refreshed_login(login, self.host)

        self.assertFalse(installed)
        self.assertEqual(self.host.stat().st_ino, inode)

    def test_install_leaves_an_unusable_host_file_alone(self) -> None:
        self.host.write_text("not json", encoding="utf-8")
        login = self.make_login(self.refreshed("t1"))

        installed = assertion_judges.install_refreshed_login(login, self.host)

        self.assertFalse(installed)
        self.assertEqual(self.host.read_text("utf-8"), "not json")

    def test_install_survives_concurrent_replacements(self) -> None:
        tokens = [f"t{number}" for number in range(1, 17)]
        logins = [self.make_login(self.refreshed(token)) for token in tokens]

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            futures = [
                pool.submit(assertion_judges.install_refreshed_login, login, self.host)
                for login in logins
            ]
            for future in futures:
                future.result()

        final = json.loads(self.host.read_text(encoding="utf-8"))
        self.assertIn(final["last_refresh"], tokens)
        self.assertEqual(final["tokens"]["refresh_token"], "R0")
        names = {path.name for path in self.host_dir.iterdir()}
        self.assertEqual(names - {"auth.json.judge-lock"}, {"auth.json"})


if __name__ == "__main__":
    unittest.main()
