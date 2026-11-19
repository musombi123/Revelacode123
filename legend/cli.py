from __future__ import annotations

import argparse
import json
from pathlib import Path

from .tlang import Composer, TLangVerifier, find_template


def main() -> None:
    parser = argparse.ArgumentParser(prog="legend", description="RevelaCode Legend deterministic template tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-library", help="verify the bundled seed library")
    run = sub.add_parser("compose", help="run one exact template by ID")
    run.add_argument("--template", required=True)
    run.add_argument("--inputs", required=True, help="JSON object")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    templates = json.loads((root / "library" / "seeds.json").read_text(encoding="utf-8"))
    if args.command == "verify-library":
        verifier, failures = TLangVerifier(), []
        for item in templates:
            report = verifier.verify(item, [x for x in templates if x["id"] != item["id"]])
            print(json.dumps(report.as_dict()))
            if not report.passed: failures.append(item["id"])
        print("verified=" + str(len(templates) - len(failures)) + "/" + str(len(templates)))
        raise SystemExit(1 if failures else 0)
    template = find_template(templates, args.template)
    if template is None:
        print(json.dumps({"status": "UNKNOWN", "template_id": args.template, "certainty": "not_established"}))
        raise SystemExit(2)
    print(json.dumps(Composer().run(template, json.loads(args.inputs)), ensure_ascii=False))


if __name__ == "__main__":
    main()
