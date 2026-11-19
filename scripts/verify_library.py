#!/usr/bin/env python3
"""Verify committed Home V-Lang records and reject duplicate IDs/fingerprints."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from legend_core.vlang import parse_document,fingerprint
paths=sorted(list((ROOT/"templates"/"library").rglob("*.vlang")) + list((ROOT/"templates"/"library").rglob("*.tlang")))
seen_ids={}; seen_fps={}
for path in paths:
    for record in parse_document(path.read_text(encoding="utf-8")):
        if record["id"] in seen_ids: raise SystemExit(f"duplicate template ID {record['id']} in {path} and {seen_ids[record['id']]}")
        fp=fingerprint(record)
        if fp in seen_fps: raise SystemExit(f"duplicate procedure {record['id']} and {seen_fps[fp]}")
        seen_ids[record["id"]]=str(path); seen_fps[fp]=record["id"]
print(f"PASS: {len(seen_ids)} unique V-Lang templates across {len(paths)} file(s)")
