from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCORING_BUILDER = REPO_ROOT / "skills" / "benchmark-scoring" / "scripts" / "build_scoring_package.py"


def load_scoring_builder():
    spec = importlib.util.spec_from_file_location("build_scoring_package", SCORING_BUILDER)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BuildScoringPackageTest(unittest.TestCase):
    def test_assertion_checks_include_criticality_falsification_and_evidence_links(self) -> None:
        module = load_scoring_builder()
        assertion_id = "csag:doc/test/assertion/A0001"
        evidence_link_id = "csag:doc/test/elink/L0001"
        extraction = {
            "title": "Test paper",
            "assertions": [
                {
                    "id": assertion_id,
                    "label": "A0001",
                    "claim_role": "result_claim",
                    "criticality": "core",
                    "assertion_text": "The treatment improves the measured outcome.",
                    "falsification_criteria": ["No independent increase in the measured outcome."],
                }
            ],
            "evidence_links": [
                {
                    "id": evidence_link_id,
                    "assertion": assertion_id,
                    "evidence_item": "csag:doc/test/evidence/E0001",
                    "strength": "strong",
                }
            ],
            "entities": [],
            "datasets": [],
            "critiques": [],
        }

        schema = module.build_schema("test", extraction, "What happened?", {"items": []})
        check = schema["assertion_checks"][0]

        self.assertEqual(check["criticality"], "core")
        self.assertEqual(check["falsification_criteria"], ["No independent increase in the measured outcome."])
        self.assertEqual(check["evidence_link_ids"], [evidence_link_id])
        self.assertEqual(check["evidence_strengths"], ["strong"])
        self.assertEqual(check["required_evidence_strength"], "moderate")
        self.assertEqual(check["weight_multiplier"], 2.0)
        self.assertTrue(check["required"])


if __name__ == "__main__":
    unittest.main()
