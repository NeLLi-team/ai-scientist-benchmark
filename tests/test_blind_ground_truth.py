"""Tests for blind_ground_truth, which writes a blinded ground truth for a case."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "skills"
    / "benchmark-prompt-package"
    / "scripts"
    / "blind_ground_truth.py"
)

SOURCE_EXTRACTION = {
    "id": "paper:synthetic",
    "title": "Synthetic paper",
    "assertions": [
        {
            "id": "csag:assertion/A0001",
            "label": "A0001",
            "assertion_text": "Bin IMGS01 encodes a capsid protein.",
            "falsification_criteria": ["No capsid hit in IMGS01.", "Hit is partial."],
            "criticality": "core",
            "claim_role": "result_claim",
            "text_spans": [{"text": "The IMGS01 bin was reported."}],
        },
        {
            "id": "csag:assertion/A0002",
            "label": "A0002",
            "assertion_text": "The second bin is smaller.",
            "falsification_criteria": ["Sizes are equal."],
            "criticality": "major",
            "claim_role": "analysis_claim",
            "text_spans": [{"text": "Size is shown in Table 1."}],
        },
    ],
    "evidence_items": [
        {
            "id": "csag:evidence/E0004",
            "label": "E0004",
            "evidence_text": "Contig IMGS01_c1 matches a capsid HMM.",
            "evidence_type": "computational",
            "text_spans": [{"text": "See the Smith lab report."}],
        },
        {
            "id": "csag:evidence/E0005",
            "evidence_text": "Plain text with no names.",
            "evidence_type": "computational",
            "text_spans": [],
        },
    ],
    "evidence_links": [
        {
            "id": "csag:link/L1",
            "assertion": "csag:assertion/A0001",
            "evidence_item": "csag:evidence/E0004",
            "polarity": "supports",
            "strength": "strong",
        },
        {
            "id": "csag:link/x/L2",
            "label": "LinkTwo",
            "assertion": "csag:assertion/A0002",
            "evidence_item": "csag:evidence/E0005",
            "polarity": "contradicts",
            "strength": "weak",
        },
    ],
}
SCHEMA_TEXT = '{"scoring": "schema"}\n'
REWRITES = {
    "assertions": {
        "A0001": {
            "assertion_text": "Bin assembly_A encodes a capsid protein.",
            "falsification_criteria": ["No capsid hit in assembly_A."],
        }
    },
    "evidence_items": {
        "E0004": {
            "evidence_text": "Contig assembly_A_contig_0001 matches a capsid HMM."
        }
    },
}


def load_module():
    spec = importlib.util.spec_from_file_location("blind_ground_truth", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BlindGroundTruthTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.module = load_module()
        self.source = self.root / "ground_truth" / "src"
        (self.source / "csag").mkdir(parents=True)
        (self.source / "scoring").mkdir()
        self.source_csag = self.source / "csag" / "paper_extraction.json"
        self.source_csag.write_text(json.dumps(SOURCE_EXTRACTION), encoding="utf-8")
        self.source_schema = self.source / "scoring" / "scoring_schema.json"
        self.source_schema.write_text(SCHEMA_TEXT, encoding="utf-8")
        self.blind = self.root / "ground_truth" / "blind"
        self.blind.mkdir()
        self.case_yaml = self.blind / "case.yaml"
        self.case_yaml.write_text(
            yaml.safe_dump(
                {
                    "case": "blind",
                    "blinded_from": "src",
                    "description": "old description",
                    "reused_ground_truth": {"source_case": "src"},
                    "other_key": "kept",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.rewrites_path = self.root / "rewrites.json"
        self.terms_path = self.root / "terms.txt"
        self.write_inputs(REWRITES, "IMGS01\n")

    def write_inputs(self, rewrites: dict, terms: str) -> None:
        self.rewrites_path.write_text(json.dumps(rewrites), encoding="utf-8")
        self.terms_path.write_text(terms, encoding="utf-8")

    def run_main(self) -> int:
        return self.module.main(
            [
                "--source-case",
                str(self.source),
                "--blind-case",
                str(self.blind),
                "--rewrites",
                str(self.rewrites_path),
                "--terms-file",
                str(self.terms_path),
            ]
        )

    def run_refused(self) -> str:
        with self.assertLogs(self.module.logger, level="ERROR") as logs:
            code = self.run_main()
        self.assertEqual(code, 2)
        self.assertFalse((self.blind / "csag").exists())
        self.assertFalse((self.blind / "scoring").exists())
        return "\n".join(logs.output)

    def output(self) -> dict:
        path = self.blind / "csag" / "paper_extraction.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_rewrites_are_applied_and_other_fields_unchanged(self) -> None:
        self.assertEqual(self.run_main(), 0)

        expected = copy.deepcopy(SOURCE_EXTRACTION)
        expected["assertions"][0].update(REWRITES["assertions"]["A0001"])
        expected["evidence_items"][0].update(REWRITES["evidence_items"]["E0004"])
        self.assertEqual(self.output(), expected)

    def test_output_json_format(self) -> None:
        self.run_main()

        text = (self.blind / "csag" / "paper_extraction.json").read_text("utf-8")
        self.assertEqual(
            text, json.dumps(self.output(), indent=2, ensure_ascii=False) + "\n"
        )

    def test_evidence_without_label_uses_last_id_segment(self) -> None:
        self.write_inputs(
            {"evidence_items": {"E0005": {"evidence_text": "Rewritten."}}}, "zzz\n"
        )

        self.assertEqual(self.run_main(), 0)

        self.assertEqual(
            self.output()["evidence_items"][1]["evidence_text"], "Rewritten."
        )

    def test_scoring_schema_is_copied_unchanged(self) -> None:
        self.run_main()

        copied = self.blind / "scoring" / "scoring_schema.json"
        self.assertEqual(copied.read_bytes(), self.source_schema.read_bytes())

    def test_case_yaml_records_four_hashes(self) -> None:
        self.run_main()

        case = yaml.safe_load(self.case_yaml.read_text(encoding="utf-8"))
        self.assertNotIn("reused_ground_truth", case)
        self.assertEqual(case["other_key"], "kept")
        self.assertNotEqual(case["description"], "old description")
        self.assertIn("blinded", case["description"])
        record = case["blinded_ground_truth"]
        expected = {
            "source_csag": self.source_csag,
            "rewrites": self.rewrites_path,
            "csag": self.blind / "csag" / "paper_extraction.json",
            "scoring_schema": self.blind / "scoring" / "scoring_schema.json",
        }
        for key, path in expected.items():
            self.assertEqual(record[key]["sha256"], sha256(path), key)
            self.assertEqual(record[key]["path"], str(path.relative_to(self.root)), key)

    def test_a_second_run_refreshes_the_recorded_hashes(self) -> None:
        self.run_main()
        changed = copy.deepcopy(REWRITES)
        changed["assertions"]["A0001"]["assertion_text"] = "A revised claim."
        self.write_inputs(changed, "IMGS01\n")

        self.assertEqual(self.run_main(), 0)

        record = yaml.safe_load(self.case_yaml.read_text(encoding="utf-8"))[
            "blinded_ground_truth"
        ]
        csag = self.blind / "csag" / "paper_extraction.json"
        self.assertEqual(record["csag"]["sha256"], sha256(csag))
        self.assertEqual(record["rewrites"]["sha256"], sha256(self.rewrites_path))

    def test_remaining_forbidden_term_is_refused_case_insensitively(self) -> None:
        self.write_inputs(
            {"assertions": {"A0001": {"assertion_text": "The imgs01 bin."}}},
            "# comment\nIMGS01\n",
        )

        message = self.run_refused()

        self.assertIn("A0001.assertion_text: IMGS01", message)
        self.assertIn("A0001.falsification_criteria: IMGS01", message)

    def test_forbidden_term_in_evidence_text_is_named(self) -> None:
        self.write_inputs({}, "capsid HMM\n")

        message = self.run_refused()

        self.assertIn("E0004.evidence_text: capsid HMM", message)

    def test_unknown_label_is_refused(self) -> None:
        self.write_inputs({"assertions": {"A9999": {"assertion_text": "x"}}}, "zzz\n")

        self.assertIn("A9999", self.run_refused())

    def test_unknown_field_is_refused(self) -> None:
        self.write_inputs({"assertions": {"A0001": {"criticality": "major"}}}, "zzz\n")

        self.assertIn("criticality", self.run_refused())

    def test_evidence_field_not_allowed_on_assertion_is_refused(self) -> None:
        self.write_inputs({"assertions": {"A0001": {"evidence_text": "x"}}}, "zzz\n")

        self.assertIn("evidence_text", self.run_refused())

    def test_terms_file_ignores_comments_and_blank_lines(self) -> None:
        self.write_inputs(REWRITES, "# IMGS01\n\n   \nunrelated\n")

        self.assertEqual(self.run_main(), 0)

    def test_empty_terms_file_is_refused(self) -> None:
        self.write_inputs(REWRITES, "# only a comment\n\n")

        self.run_refused()

    def test_term_only_in_text_spans_does_not_block(self) -> None:
        self.write_inputs(REWRITES, "Smith lab\nThe IMGS01 bin was reported\n")

        self.assertEqual(self.run_main(), 0)
        self.assertIn("Smith lab", json.dumps(self.output()))

    def set_source(self, mutate) -> None:
        extraction = copy.deepcopy(SOURCE_EXTRACTION)
        mutate(extraction)
        self.source_csag.write_text(json.dumps(extraction), encoding="utf-8")

    def test_assertion_label_is_checked(self) -> None:
        self.write_inputs({}, "A0002\n")

        self.assertIn("A0002.label: A0002", self.run_refused())

    def test_assertion_claim_role_is_checked(self) -> None:
        self.write_inputs({}, "Analysis_Claim\n")

        self.assertIn("A0002.claim_role: Analysis_Claim", self.run_refused())

    def test_link_polarity_and_strength_are_checked(self) -> None:
        self.write_inputs({}, "supports\nweak\n")

        message = self.run_refused()

        self.assertIn("L1.polarity: supports", message)
        self.assertIn("LinkTwo.strength: weak", message)

    def test_same_source_and_blind_case_is_refused(self) -> None:
        shutil.copyfile(self.case_yaml, self.source / "case.yaml")
        before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.blind = self.root / "ground_truth" / ".." / "ground_truth" / "src"

        with self.assertLogs(self.module.logger, level="ERROR") as logs:
            code = self.run_main()

        self.assertEqual(code, 2)
        self.assertIn("same", "\n".join(logs.output))
        after = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_failure_leaves_an_earlier_output_untouched(self) -> None:
        self.run_main()
        files = [
            self.case_yaml,
            self.blind / "csag" / "paper_extraction.json",
            self.blind / "scoring" / "scoring_schema.json",
        ]
        before = [path.read_bytes() for path in files]
        self.write_inputs(REWRITES, "assembly_A\n")

        self.assertEqual(self.run_main(), 2)

        self.assertEqual([path.read_bytes() for path in files], before)

    def test_unusable_case_yaml_writes_nothing(self) -> None:
        for text in ("- a list\n", "case: x\n", "key: [unclosed\n"):
            with self.subTest(text=text):
                self.case_yaml.write_text(text, encoding="utf-8")

                self.run_refused()

    def test_unparsable_inputs_are_refused(self) -> None:
        for path in (self.source_csag, self.rewrites_path):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_text("{not json", encoding="utf-8")

                self.run_refused()

                path.write_bytes(original)

    def test_duplicate_labels_are_refused(self) -> None:
        def duplicate_assertion(extraction: dict) -> None:
            extraction["assertions"][1]["label"] = "A0001"

        self.set_source(duplicate_assertion)
        self.write_inputs(REWRITES, "zzz\n")

        self.assertIn("A0001", self.run_refused())

    def test_fallback_label_that_collides_is_refused(self) -> None:
        def collide(extraction: dict) -> None:
            extraction["evidence_items"][1]["id"] = "csag:evidence/E0004"

        self.set_source(collide)
        self.write_inputs(REWRITES, "zzz\n")

        self.assertIn("E0004", self.run_refused())

    def test_bad_value_types_are_refused_with_label_and_field(self) -> None:
        cases = {
            "A0001.assertion_text": {"A0001": {"assertion_text": ""}},
            "A0002.assertion_text": {"A0002": {"assertion_text": 5}},
            "A0001.falsification_criteria": {
                "A0001": {"falsification_criteria": "one string"}
            },
            "A0002.falsification_criteria": {
                "A0002": {"falsification_criteria": ["ok", ""]}
            },
        }
        for expected, assertions in cases.items():
            with self.subTest(expected=expected):
                self.write_inputs({"assertions": assertions}, "zzz\n")

                self.assertIn(expected, self.run_refused())

    def test_bad_evidence_text_is_refused(self) -> None:
        self.write_inputs(
            {"evidence_items": {"E0005": {"evidence_text": " "}}}, "zzz\n"
        )

        self.assertIn("E0005.evidence_text", self.run_refused())

    def test_source_value_of_the_wrong_type_is_refused(self) -> None:
        def break_source(extraction: dict) -> None:
            extraction["assertions"][1]["falsification_criteria"] = None

        self.set_source(break_source)

        self.assertIn("A0002.falsification_criteria", self.run_refused())

    def test_rewrite_entry_that_is_not_an_object_is_refused(self) -> None:
        self.write_inputs({"assertions": {"A0001": "text"}}, "zzz\n")

        self.assertIn("A0001", self.run_refused())


if __name__ == "__main__":
    unittest.main()
