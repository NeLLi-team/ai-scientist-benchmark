from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTICLE_EXTRACTION = REPO_ROOT / "skills" / "paper-to-md" / "scripts" / "article_extraction.py"


def load_article_extraction():
    spec = importlib.util.spec_from_file_location("article_extraction", ARTICLE_EXTRACTION)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ArticleExtractionTest(unittest.TestCase):
    def test_load_clean_lines_skips_yaml_front_matter(self) -> None:
        module = load_article_extraction()
        with tempfile.TemporaryDirectory() as tmpdir:
            markdown_path = Path(tmpdir) / "paper.md"
            markdown_path.write_text(
                "\n".join(
                    [
                        "---",
                        "title: OCR metadata title",
                        "source: temporary conversion metadata",
                        "---",
                        "# Paper Title",
                        "",
                        "Abstract text.",
                    ]
                ),
                encoding="utf-8",
            )

            lines = module.load_clean_lines(markdown_path)

        self.assertNotIn("title: OCR metadata title", lines)
        self.assertNotIn("source: temporary conversion metadata", lines)
        self.assertEqual(lines[0], "# Paper Title")


if __name__ == "__main__":
    unittest.main()
