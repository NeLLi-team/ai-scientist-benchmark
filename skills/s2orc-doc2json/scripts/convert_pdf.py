#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "s2orc-doc2json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a PDF to S2ORC JSON, TEI XML, and Markdown.")
    parser.add_argument("input_pdf", help="Path to the input PDF")
    parser.add_argument("--output-dir", required=True, help="Directory for final outputs")
    parser.add_argument("--temp-dir", help="Optional temp directory for TEI generation")
    parser.add_argument("--grobid-server", default="127.0.0.1", help="Grobid host")
    parser.add_argument("--grobid-port", default=8070, type=int, help="Grobid port")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the scratch temp directory")
    parser.add_argument("--skip-markdown", action="store_true", help="Do not render Markdown")
    return parser.parse_args()


def add_backend_to_path() -> None:
    backend_path = str(BACKEND_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


def main() -> int:
    args = parse_args()
    input_pdf = Path(args.input_pdf).resolve()
    if not input_pdf.exists():
        raise FileNotFoundError(f"Input PDF does not exist: {input_pdf}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(args.temp_dir).resolve() if args.temp_dir else output_dir / "_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    stem = input_pdf.stem

    add_backend_to_path()
    from doc2json.grobid2json.process_pdf import process_pdf_file
    from s2orc_json_to_markdown import build_markdown

    grobid_config = {
        "grobid_server": args.grobid_server,
        "grobid_port": str(args.grobid_port),
        "batch_size": 1000,
        "sleep_time": 5,
        "generateIDs": False,
        "consolidate_header": False,
        "consolidate_citations": False,
        "include_raw_citations": True,
        "include_raw_affiliations": False,
        "max_workers": 2,
    }

    raw_json_path = Path(
        process_pdf_file(
            str(input_pdf),
            temp_dir=str(temp_dir),
            output_dir=str(output_dir),
            grobid_config=grobid_config,
        )
    ).resolve()

    final_json_path = output_dir / f"{stem}.s2orc.json"
    if raw_json_path != final_json_path:
        if final_json_path.exists():
            final_json_path.unlink()
        raw_json_path.rename(final_json_path)

    tei_temp_path = temp_dir / f"{stem}.tei.xml"
    final_tei_path = output_dir / f"{stem}.tei.xml"
    if tei_temp_path.exists():
        shutil.copy2(tei_temp_path, final_tei_path)

    if not args.skip_markdown:
        import json

        markdown = build_markdown(json.loads(final_json_path.read_text(encoding="utf-8")))
        markdown_path = output_dir / f"{stem}.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        print(markdown_path)

    print(final_json_path)
    if final_tei_path.exists():
        print(final_tei_path)

    if not args.keep_temp and temp_dir.exists():
        shutil.rmtree(temp_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
