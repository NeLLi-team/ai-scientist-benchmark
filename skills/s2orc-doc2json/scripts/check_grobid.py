#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check that a local Grobid service is reachable.")
    parser.add_argument("--host", default="127.0.0.1", help="Grobid host")
    parser.add_argument("--port", default=8070, type=int, help="Grobid port")
    parser.add_argument("--timeout", default=5.0, type=float, help="HTTP timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = f"http://{args.host}:{args.port}/api/isalive"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8", errors="replace").strip()
    except urllib.error.URLError as exc:
        print(f"Grobid check failed: {url} is not reachable: {exc}", file=sys.stderr)
        return 1

    print(f"Grobid reachable: {url}")
    if body:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
