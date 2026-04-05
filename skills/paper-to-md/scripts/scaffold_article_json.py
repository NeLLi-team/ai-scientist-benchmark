#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_template() -> dict:
    return {
        "title": "",
        "authors": "",
        "affiliations": "",
        "abstract": "",
        "main": "",
        "methods": "",
        "figure_legends": [],
        "figure_interpretation": "",
        "references": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scaffold a blank Article-schema JSON file from an OCR markdown path."
    )
    parser.add_argument("markdown_path", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    markdown_path = args.markdown_path.expanduser().resolve()
    if not markdown_path.exists():
        raise FileNotFoundError(markdown_path)

    output_path = args.output.expanduser().resolve() if args.output else markdown_path.with_suffix('.article.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(build_template(), indent=2) + '\n', encoding='utf-8')
    print(output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
