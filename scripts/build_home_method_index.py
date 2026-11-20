#!/usr/bin/env python3
"""Build/check Home's metadata-only method index from the trusted V-Lang library."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vorax.home_index import build_method_index
from vorax.vlang import parse_document

LIBRARY = ROOT / "templates" / "library"
TARGET = LIBRARY / "index.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    records = []
    for pattern in ("*.vlang", "*.tlang"):
        for path in sorted(LIBRARY.rglob(pattern)):
            records.extend(parse_document(path.read_text(encoding="utf-8")))

    expected = json.dumps(build_method_index(records), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not TARGET.is_file():
            raise SystemExit("Home metadata index is missing")
        actual = TARGET.read_text(encoding="utf-8")
        if actual != expected:
            raise SystemExit("Home metadata index is stale or contains non-derived data")
        print(f"PASS: Home metadata-only index matches {len(records)} trusted V-Lang methods")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(expected, encoding="utf-8")
    print(f"Updated {TARGET} with {len(records)} metadata-only method entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
