"""Tests for review_ground_truth.py: the review sheet and the signed sign-off.

Everything runs on a small fixture case in a temporary directory. The real
06mega ground truth is only read, never written.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "benchmark-scoring" / "scripts"
MEGA_CASE = REPO_ROOT / "ground_truth" / "06mega"

sys.path.insert(0, str(SCRIPTS_DIR))
import review_ground_truth  # noqa: E402  # needs the scripts dir on sys.path first

DOC = "csag:doc/test"


def make_extraction() -> dict:
    """Three assertions, two evidence items, three links."""
    return {
        "id": DOC,
        "title": "Test paper",
        "assertions": [
            {
                "id": f"{DOC}/assertion/A0001",
                "label": "A0001",
                "assertion_text": "The virus genome is near 5 Mb.",
                "claim_role": "discovery",
                "criticality": "core",
                "falsification_criteria": ["Closed genomes are much smaller."],
                "text_spans": [{"exact_text": "genomes of up to 5 Mb"}],
            },
            {
                "id": f"{DOC}/assertion/A0002",
                "label": "A0002",
                "assertion_text": "Paralogs drive\ngenome expansion.",
                "claim_role": "conclusion",
                "criticality": "major",
                "falsification_criteria": ["No paralog signal.", "Second criterion."],
                "text_spans": [],
            },
            {
                "id": f"{DOC}/assertion/A0003",
                "label": "A0003",
                "assertion_text": "The bins are metagenome-assembled.",
                "claim_role": "limitation",
                "criticality": "supporting",
                "falsification_criteria": ["A closed genome exists."],
                "text_spans": [],
            },
        ],
        "evidence_items": [
            {
                "id": f"{DOC}/evidence/E0001",
                "label": "E0001",
                "evidence_type": "observational_data",
                "evidence_text": "Assembly sizes are 4.63 and 4.34 Mb.",
                "text_spans": [{"exact_text": "assembled sizes of 4.63 Mb"}],
            },
            {
                "id": f"{DOC}/evidence/E0002",
                "label": "E0002",
                "evidence_type": "computational_result",
                "evidence_text": "Paralog families have high copy number.",
                "text_spans": [],
            },
        ],
        "evidence_links": [
            {
                "id": f"{DOC}/elink/L0001",
                "label": "L0001",
                "evidence_item": f"{DOC}/evidence/E0001",
                "assertion": f"{DOC}/assertion/A0001",
                "polarity": "supports",
                "strength": "strong",
            },
            {
                "id": f"{DOC}/elink/L0002",
                "label": "L0002",
                "evidence_item": f"{DOC}/evidence/E0002",
                "assertion": f"{DOC}/assertion/A0002",
                "polarity": "weakens",
                "strength": "moderate",
            },
            {
                "id": f"{DOC}/elink/L0003",
                "label": "L0003",
                "evidence_item": f"{DOC}/evidence/E0001",
                "assertion": f"{DOC}/assertion/A0003",
                "polarity": "supports",
                "strength": "weak",
            },
        ],
    }


def fill(sheet: str, label: str, decision: str, criticality: str, note: str) -> str:
    """Fill the decision, criticality and note lines of one assertion section."""
    head, found, tail = sheet.partition(f"## {label}\n")
    assert found, f"no section {label}"
    section, sep, rest = tail.partition("\n## ")
    section = re.sub(r"(?m)^decision:.*$", f"decision: {decision}", section)
    section = re.sub(r"(?m)^criticality:.*$", f"criticality: {criticality}", section)
    section = re.sub(r"(?ms)^note:.*\Z", f"note: {note}\n", section)
    return head + found + section + sep + rest


class CaseTestCase(unittest.TestCase):
    """A fixture case directory with a ground-truth CSAG."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.case_dir = Path(self._tmp.name) / "06test"
        (self.case_dir / "csag").mkdir(parents=True)
        self.extraction_path = self.case_dir / "csag" / "paper_extraction.json"
        self.extraction_path.write_text(json.dumps(make_extraction()), encoding="utf-8")
        self.sheet_path = Path(self._tmp.name) / "sheet.md"
        self.signoff_path = self.case_dir / "csag" / "signoff.json"

    def extraction_sha(self) -> str:
        return hashlib.sha256(self.extraction_path.read_bytes()).hexdigest()

    def packet(self) -> str:
        review_ground_truth.write_packet(self.case_dir, self.sheet_path)
        return self.sheet_path.read_text(encoding="utf-8")

    def approve_all(self, sheet: str) -> str:
        for label, criticality in (
            ("A0001", "core"),
            ("A0002", "major"),
            ("A0003", "supporting"),
        ):
            sheet = fill(sheet, label, "approve", criticality, "")
        return sheet

    def sign(self, sheet: str, reviewer: str = "Reviewer One") -> dict:
        self.sheet_path.write_text(sheet, encoding="utf-8")
        review_ground_truth.sign_off(self.case_dir, self.sheet_path, reviewer=reviewer)
        return json.loads(self.signoff_path.read_text(encoding="utf-8"))

    def assert_refused(self, sheet: str, message: str, reviewer: str = "R") -> None:
        self.sheet_path.write_text(sheet, encoding="utf-8")
        with self.assertRaises(review_ground_truth.ReviewError) as raised:
            review_ground_truth.sign_off(
                self.case_dir, self.sheet_path, reviewer=reviewer
            )
        self.assertIn(message, str(raised.exception))
        self.assertFalse(self.signoff_path.exists())


class PacketTest(CaseTestCase):
    def test_packet_has_one_section_per_assertion_in_order(self) -> None:
        sheet = self.packet()

        self.assertEqual(
            re.findall(r"(?m)^## (\S+)$", sheet), ["A0001", "A0002", "A0003"]
        )

    def test_packet_shows_text_role_criticality_and_criteria(self) -> None:
        sheet = self.packet()

        self.assertIn("The virus genome is near 5 Mb.", sheet)
        self.assertIn("claim_role: discovery", sheet)
        self.assertIn("current_criticality: core", sheet)
        self.assertIn("Closed genomes are much smaller.", sheet)
        self.assertIn("Second criterion.", sheet)

    def test_packet_puts_multi_line_assertion_text_on_one_line(self) -> None:
        self.assertIn("- text: Paralogs drive genome expansion.", self.packet())

    def test_packet_quotes_the_assertion_source_span(self) -> None:
        self.assertIn("> genomes of up to 5 Mb", self.packet())

    def test_packet_lists_linked_evidence_with_polarity_strength_and_spans(
        self,
    ) -> None:
        section = self.packet().split("## A0001\n")[1].split("\n## ")[0]

        self.assertIn("L0001", section)
        self.assertIn("supports", section)
        self.assertIn("strong", section)
        self.assertIn("Assembly sizes are 4.63 and 4.34 Mb.", section)
        self.assertIn("> assembled sizes of 4.63 Mb", section)

    def test_packet_shows_only_the_evidence_linked_to_that_assertion(self) -> None:
        section = self.packet().split("## A0001\n")[1].split("\n## ")[0]

        self.assertNotIn("Paralog families have high copy number.", section)

    def test_packet_notes_an_assertion_without_evidence_links(self) -> None:
        extraction = make_extraction()
        extraction["evidence_links"] = []
        self.extraction_path.write_text(json.dumps(extraction), encoding="utf-8")

        self.assertIn("(none linked)", self.packet())

    def test_packet_decision_lines_are_blank_for_the_reviewer(self) -> None:
        sheet = self.packet()

        self.assertEqual(len(re.findall(r"(?m)^decision:$", sheet)), 3)
        self.assertEqual(len(re.findall(r"(?m)^note:$", sheet)), 3)
        self.assertIn("approve | edit | reject", sheet)

    def test_packet_prefills_criticality_with_the_current_value(self) -> None:
        sheet = self.packet()

        self.assertEqual(
            re.findall(r"(?m)^criticality: (\S+)$", sheet),
            ["core", "major", "supporting"],
        )

    def test_packet_records_the_case_and_the_ground_truth_hash(self) -> None:
        sheet = self.packet()

        self.assertIn("- case_id: 06test", sheet)
        self.assertIn(f"- ground_truth_sha256: {self.extraction_sha()}", sheet)

    def test_packet_leaves_the_ground_truth_unchanged(self) -> None:
        before = self.extraction_sha()

        self.packet()

        self.assertEqual(self.extraction_sha(), before)


class SignTest(CaseTestCase):
    def test_sign_writes_signoff_json_in_the_spec_format(self) -> None:
        sheet = self.packet()
        sheet = fill(sheet, "A0001", "approve", "core", "")
        sheet = fill(sheet, "A0002", "edit", "core", "Central claim of the paper.")
        sheet = fill(sheet, "A0003", "reject", "supporting", "Not a claim.")

        before = datetime.now(UTC).replace(microsecond=0)
        signoff = self.sign(sheet, reviewer="Reviewer One")

        self.assertEqual(
            set(signoff),
            {"case_id", "ground_truth_sha256", "reviewer", "signed_at", "assertions"},
        )
        self.assertEqual(signoff["case_id"], "06test")
        self.assertEqual(signoff["ground_truth_sha256"], self.extraction_sha())
        self.assertEqual(signoff["reviewer"], "Reviewer One")
        self.assertEqual(
            signoff["assertions"],
            {
                "A0001": {"decision": "approve", "criticality": "core", "note": ""},
                "A0002": {
                    "decision": "edit",
                    "criticality": "core",
                    "note": "Central claim of the paper.",
                },
                "A0003": {
                    "decision": "reject",
                    "criticality": "supporting",
                    "note": "Not a claim.",
                },
            },
        )
        signed_at = datetime.fromisoformat(signoff["signed_at"])
        self.assertEqual(signed_at.utcoffset().total_seconds(), 0)
        self.assertGreaterEqual(signed_at, before)

    def test_sign_does_not_touch_the_ground_truth(self) -> None:
        before = self.extraction_sha()

        self.sign(self.approve_all(self.packet()))

        self.assertEqual(self.extraction_sha(), before)

    def test_sign_keeps_a_multi_line_note(self) -> None:
        sheet = fill(
            self.approve_all(self.packet()),
            "A0002",
            "edit",
            "major",
            "First line.\nSecond line.",
        )

        signoff = self.sign(sheet)

        self.assertEqual(
            signoff["assertions"]["A0002"]["note"], "First line.\nSecond line."
        )

    def test_sign_uses_the_current_criticality_when_the_line_is_blank(self) -> None:
        sheet = fill(self.approve_all(self.packet()), "A0001", "approve", "", "")

        signoff = self.sign(sheet)

        self.assertEqual(signoff["assertions"]["A0001"]["criticality"], "core")

    def test_sign_accepts_decisions_in_any_case_and_spacing(self) -> None:
        sheet = fill(self.approve_all(self.packet()), "A0001", "  Approve ", "core", "")

        signoff = self.sign(sheet)

        self.assertEqual(signoff["assertions"]["A0001"]["decision"], "approve")

    def test_sign_replaces_an_earlier_signoff(self) -> None:
        self.sign(self.approve_all(self.packet()), reviewer="First")

        signoff = self.sign(self.approve_all(self.packet()), reviewer="Second")

        self.assertEqual(signoff["reviewer"], "Second")

    def test_sign_refuses_a_blank_decision(self) -> None:
        sheet = fill(self.approve_all(self.packet()), "A0002", "", "major", "")

        self.assert_refused(sheet, "A0002")

    def test_sign_refuses_the_unfilled_packet(self) -> None:
        self.assert_refused(self.packet(), "A0001")

    def test_sign_refuses_an_unknown_decision(self) -> None:
        sheet = fill(
            self.approve_all(self.packet()), "A0003", "maybe", "supporting", ""
        )

        self.assert_refused(sheet, "maybe")

    def test_sign_refuses_an_unknown_criticality(self) -> None:
        sheet = fill(self.approve_all(self.packet()), "A0003", "edit", "crucial", "")

        self.assert_refused(sheet, "crucial")

    def test_sign_refuses_an_approval_that_changes_the_criticality(self) -> None:
        sheet = fill(self.approve_all(self.packet()), "A0003", "approve", "core", "")

        self.assert_refused(sheet, "A0003")

    def test_sign_refuses_a_sheet_made_from_another_version_of_the_ground_truth(
        self,
    ) -> None:
        sheet = self.approve_all(self.packet())
        extraction = make_extraction()
        extraction["assertions"][0]["assertion_text"] = "Changed after the packet."
        self.extraction_path.write_text(json.dumps(extraction), encoding="utf-8")

        self.assert_refused(sheet, "ground truth changed")

    def test_sign_refuses_a_sheet_that_misses_an_assertion(self) -> None:
        sheet = self.approve_all(self.packet())
        head = sheet.partition("## A0003\n")[0]

        self.assert_refused(head, "A0003")

    def test_sign_refuses_a_section_for_an_unknown_assertion(self) -> None:
        sheet = self.approve_all(self.packet()) + "\n## A0099\ndecision: approve\n"

        self.assert_refused(sheet, "A0099")

    def test_sign_refuses_a_repeated_section(self) -> None:
        sheet = self.approve_all(self.packet())
        section = "## A0001\n" + sheet.split("## A0001\n")[1].split("\n## ")[0]

        self.assert_refused(sheet + "\n" + section + "\n", "A0001")

    def test_sign_refuses_a_section_that_repeats_the_decision_line(self) -> None:
        sheet = self.approve_all(self.packet()).replace(
            "decision: approve\n", "decision: approve\ndecision: reject\n", 1
        )

        self.assert_refused(sheet, "A0001: decision line appears more than once")

    def test_sign_refuses_a_section_that_repeats_the_criticality_line(self) -> None:
        sheet = self.approve_all(self.packet()).replace(
            "criticality: major\n", "criticality: major\ncriticality: core\n", 1
        )

        self.assert_refused(sheet, "A0002: criticality line appears more than once")

    def test_sign_refuses_a_blank_reviewer(self) -> None:
        self.assert_refused(self.approve_all(self.packet()), "reviewer", reviewer="  ")

    def test_sign_refuses_a_missing_ground_truth(self) -> None:
        sheet = self.approve_all(self.packet())
        self.extraction_path.unlink()

        self.assert_refused(sheet, "paper_extraction.json")


class CommandLineTest(CaseTestCase):
    def run_main(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = review_ground_truth.main(list(argv))
        return code, err.getvalue()

    def test_packet_then_sign_through_the_command_line(self) -> None:
        code, _ = self.run_main(
            "packet", "--case-dir", str(self.case_dir), "--out", str(self.sheet_path)
        )
        self.sheet_path.write_text(
            self.approve_all(self.sheet_path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        sign_code, _ = self.run_main(
            "sign",
            "--case-dir", str(self.case_dir),
            "--sheet", str(self.sheet_path),
            "--reviewer", "Reviewer One",
        )  # fmt: skip

        self.assertEqual((code, sign_code), (0, 0))
        signoff = json.loads(self.signoff_path.read_text(encoding="utf-8"))
        self.assertEqual(signoff["reviewer"], "Reviewer One")

    def test_sign_with_a_blank_decision_exits_2_and_writes_nothing(self) -> None:
        self.run_main(
            "packet", "--case-dir", str(self.case_dir), "--out", str(self.sheet_path)
        )

        code, message = self.run_main(
            "sign",
            "--case-dir", str(self.case_dir),
            "--sheet", str(self.sheet_path),
            "--reviewer", "R",
        )  # fmt: skip

        self.assertEqual(code, 2)
        self.assertIn("A0001", message)
        self.assertFalse(self.signoff_path.exists())

    def test_a_missing_case_dir_exits_2(self) -> None:
        code, message = self.run_main(
            "packet", "--case-dir", "/no/such/case", "--out", str(self.sheet_path)
        )

        self.assertEqual(code, 2)
        self.assertIn("paper_extraction.json", message)


@unittest.skipUnless(MEGA_CASE.is_dir(), "06mega ground truth not present")
class MegaPacketTest(unittest.TestCase):
    def test_packet_for_06mega_covers_every_assertion(self) -> None:
        extraction = json.loads(
            (MEGA_CASE / "csag" / "paper_extraction.json").read_text(encoding="utf-8")
        )
        labels = [assertion["label"] for assertion in extraction["assertions"]]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sheet.md"

            review_ground_truth.write_packet(MEGA_CASE, out)
            sheet = out.read_text(encoding="utf-8")

        self.assertEqual(re.findall(r"(?m)^## (\S+)$", sheet), labels)
        self.assertEqual(len(re.findall(r"(?m)^decision:$", sheet)), len(labels))


if __name__ == "__main__":
    unittest.main()
