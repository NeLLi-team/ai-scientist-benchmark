#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render PDF pages to PNG files for figure review."
    )
    parser.add_argument("input_pdf", type=Path, help="Path to the input PDF")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for PNG output")
    parser.add_argument("--dpi", type=int, default=144, help="Rendering DPI (default: 144)")
    parser.add_argument("--first-page", type=int, default=None, help="First page to render (1-based)")
    parser.add_argument("--last-page", type=int, default=None, help="Last page to render (1-based)")
    parser.add_argument("--prefix", default="page", help="PNG filename prefix (default: page)")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.dpi <= 0:
        raise SystemExit("--dpi must be positive")
    if args.first_page is not None and args.first_page <= 0:
        raise SystemExit("--first-page must be positive")
    if args.last_page is not None and args.last_page <= 0:
        raise SystemExit("--last-page must be positive")
    if (
        args.first_page is not None
        and args.last_page is not None
        and args.first_page > args.last_page
    ):
        raise SystemExit("--first-page cannot be greater than --last-page")


def render_pages(args: argparse.Namespace) -> list[Path]:
    input_pdf = args.input_pdf.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not input_pdf.exists():
        raise SystemExit(f"Input PDF does not exist: {input_pdf}")

    output_dir.mkdir(parents=True, exist_ok=True)
    prefix_path = output_dir / args.prefix

    cmd = ["pdftoppm", "-png", "-r", str(args.dpi)]
    if args.first_page is not None:
        cmd.extend(["-f", str(args.first_page)])
    if args.last_page is not None:
        cmd.extend(["-l", str(args.last_page)])
    cmd.extend([str(input_pdf), str(prefix_path)])

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "pdftoppm failed")

    pattern = re.compile(rf"^{re.escape(args.prefix)}-(\d+)\.png$")
    pngs = []
    for path in sorted(output_dir.glob(f"{args.prefix}-*.png")):
        if pattern.match(path.name):
            pngs.append(path)
    if not pngs:
        raise SystemExit("No PNG files were produced")
    return pngs


def write_manifest(output_dir: Path, pngs: list[Path]) -> Path:
    manifest_path = output_dir / "render_manifest.json"
    manifest = {
        "page_count": len(pngs),
        "pages": [
            {
                "path": str(path),
                "name": path.name,
            }
            for path in pngs
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    args = parse_args()
    validate_args(args)
    pngs = render_pages(args)
    manifest_path = write_manifest(args.output_dir.expanduser().resolve(), pngs)
    for path in pngs:
        print(path)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
