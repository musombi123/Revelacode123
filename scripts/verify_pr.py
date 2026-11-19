from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import PurePosixPath

from legend.home import HomeGate, load_trusted_children


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def read_at(ref: str, path: str) -> dict:
    return json.loads(git("show", ref + ":" + path))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    changes = git("diff", "--name-status", args.base + "..." + args.head).splitlines()
    if not changes:
        print("REJECT: pull request contains no changes")
        return 1
    paths = []
    for change in changes:
        fields = change.split("\t")
        if len(fields) != 2 or fields[0] != "A":
            print("REJECT: Home accepts only newly added template files; found " + change)
            return 1
        path = fields[1]
        pure = PurePosixPath(path)
        if pure.parent.as_posix() != "library/templates" or pure.suffix != ".json" or len(pure.parts) != 3:
            print("REJECT: path outside library/templates/*.json: " + path)
            return 1
        paths.append(path)
    try:
        seeds = read_at(args.base, "library/seeds.json")
        registry = read_at(args.base, "library/trust/children.json")
        trusted = load_trusted_children()
    except Exception as exc:
        print("REJECT: Home configuration is invalid: " + str(exc))
        return 1
    gate = HomeGate(trusted, set(registry.get("blocked_children", [])))
    existing = list(seeds)
    existing_paths = git("ls-tree", "-r", "--name-only", args.base, "library/templates").splitlines()
    for path in existing_paths:
        if path.endswith(".json"):
            try: existing.append(read_at(args.base, path))
            except Exception:
                print("REJECT: could not read existing Home template: " + path)
                return 1
    failures = []
    for path in paths:
        try:
            proposal = read_at(args.head, path)
            result = gate.verify(proposal, existing)
            if not result["passed"]:
                failures.append({"path": path, **result})
                continue
            if path != "library/templates/" + proposal["id"] + ".json":
                failures.append({"path": path, "issues": ["filename must match template ID"]})
                continue
            existing.append(proposal)
            print("PASS " + path + " child=" + str(result["child_id"]) + " tests=" + str(result["tests_passed"]) + "/" + str(result["tests_total"]))
        except Exception as exc: failures.append({"path": path, "issues": [str(exc)]})
    if failures:
        print("REJECT " + json.dumps(failures, ensure_ascii=False))
        return 1
    print("HOME_GATE_PASS templates=" + str(len(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
