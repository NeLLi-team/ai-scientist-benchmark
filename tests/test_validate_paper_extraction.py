from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "skills" / "csag-extraction" / "scripts" / "validate_paper_extraction.py"


def base_extraction() -> dict:
    return {
        "id": "csag:doc/test",
        "title": "Test paper",
        "doi": "",
        "pmid": "",
        "assertions": [
            {
                "id": "csag:doc/test/assertion/A0001",
                "label": "A0001",
                "assertion_text": "The treatment improves the measured outcome.",
                "claim_role": "result_claim",
                "normalization_status": "raw",
                "contexts": [{"id": "csag:doc/test/context/C0001", "label": "test context"}],
            }
        ],
        "evidence_items": [
            {
                "id": "csag:doc/test/evidence/E0001",
                "label": "E0001",
                "evidence_type": "experimental_result",
                "evidence_text": "The measured outcome increased.",
            }
        ],
        "evidence_links": [
            {
                "id": "csag:doc/test/elink/L0001",
                "evidence_item": "csag:doc/test/evidence/E0001",
                "assertion": "csag:doc/test/assertion/A0001",
                "polarity": "supports",
            }
        ],
        "extraction_activities": [
            {
                "id": "csag:doc/test/activity/test",
                "parameters": [
                    {"key": "doi_status", "value": "unresolved"},
                    {"key": "pmid_status", "value": "unresolved"},
                ],
            }
        ],
    }


class ValidatePaperExtractionTest(unittest.TestCase):
    def run_validator(self, extraction: dict, profile: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            extraction_path = root / "paper_extraction.json"
            report_path = root / "report.json"
            extraction_path.write_text(json.dumps(extraction), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    str(extraction_path),
                    "--profile",
                    profile,
                    "--report-out",
                    str(report_path),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            return result, report

    def test_candidate_profile_preserves_legacy_assertion_shape(self) -> None:
        result, report = self.run_validator(base_extraction(), "candidate")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["profile"], "candidate")

    def test_candidate_profile_rejects_missing_required_evidence_link_refs(self) -> None:
        extraction = base_extraction()
        del extraction["evidence_links"][0]["evidence_item"]

        result, report = self.run_validator(extraction, "candidate")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertIn("evidence_item is missing", "\n".join(report["errors"]))

    def test_ground_truth_profile_requires_assertion_evaluation_metadata(self) -> None:
        result, report = self.run_validator(base_extraction(), "ground_truth")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertIn("missing criticality", "\n".join(report["errors"]))
        self.assertIn("missing falsification_criteria", "\n".join(report["errors"]))

    def test_ground_truth_profile_accepts_complete_assertion_metadata(self) -> None:
        extraction = base_extraction()
        assertion = extraction["assertions"][0]
        assertion["criticality"] = "core"
        assertion["falsification_criteria"] = [
            "The claim is weakened if independent analysis shows no increase in the measured outcome."
        ]
        assertion["text_spans"] = [
            {
                "id": "csag:doc/test/span/A0001",
                "document_id": "csag:doc/test",
                "section_type": "results",
                "start_char": 0,
                "end_char": 42,
            }
        ]
        extraction["evidence_links"][0]["strength"] = "strong"
        extraction["evidence_links"][0]["rationale"] = "The measured increase directly supports the assertion."

        result, report = self.run_validator(extraction, "ground_truth")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["profile"], "ground_truth")

    def test_candidate_profile_rejects_dangling_artifact_references(self) -> None:
        extraction = base_extraction()
        extraction["artifacts"] = [
            {
                "id": "csag:doc/test/artifact/F0001",
                "artifact_type": "figure",
                "artifact_label": "Figure 1",
            }
        ]
        extraction["evidence_items"][0]["associated_artifacts"] = ["csag:doc/test/artifact/MISSING"]

        result, report = self.run_validator(extraction, "candidate")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertIn("associated_artifacts references missing id", "\n".join(report["errors"]))

    def test_ground_truth_profile_requires_grounding_for_supporting_assertions(self) -> None:
        extraction = base_extraction()
        assertion = extraction["assertions"][0]
        assertion["criticality"] = "supporting"
        assertion["falsification_criteria"] = [
            "The claim is weakened if independent analysis shows no increase in the measured outcome."
        ]
        extraction["evidence_links"][0]["strength"] = "moderate"
        extraction["evidence_links"][0]["rationale"] = "The measured increase supports the assertion."

        result, report = self.run_validator(extraction, "ground_truth")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertIn("lacks assertion/evidence text_spans", "\n".join(report["errors"]))

    def test_ground_truth_profile_rejects_core_claim_with_only_weak_evidence(self) -> None:
        extraction = base_extraction()
        assertion = extraction["assertions"][0]
        assertion["criticality"] = "core"
        assertion["falsification_criteria"] = [
            "The claim is weakened if independent analysis shows no increase in the measured outcome."
        ]
        assertion["text_spans"] = [
            {
                "id": "csag:doc/test/span/A0001",
                "document_id": "csag:doc/test",
                "section_type": "results",
                "start_char": 0,
                "end_char": 42,
            }
        ]
        extraction["evidence_links"][0]["strength"] = "weak"
        extraction["evidence_links"][0]["rationale"] = "The measured increase weakly supports the assertion."

        result, report = self.run_validator(extraction, "ground_truth")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertIn("has no strong enough decisive evidence_link", "\n".join(report["errors"]))

    def test_ground_truth_profile_allows_weak_evidence_for_explicit_limitation(self) -> None:
        extraction = base_extraction()
        assertion = extraction["assertions"][0]
        assertion["claim_role"] = "limitation"
        assertion["criticality"] = "major"
        assertion["falsification_criteria"] = [
            "The limitation is weakened if independent analysis shows the measured outcome is fully resolved."
        ]
        assertion["text_spans"] = [
            {
                "id": "csag:doc/test/span/A0001",
                "document_id": "csag:doc/test",
                "section_type": "discussion",
                "start_char": 0,
                "end_char": 42,
            }
        ]
        extraction["evidence_links"][0]["strength"] = "weak"
        extraction["evidence_links"][0]["rationale"] = "The evidence weakly supports this limitation."

        result, report = self.run_validator(extraction, "ground_truth")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
