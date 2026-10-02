#!/usr/bin/env python3
"""Validate GitHub Actions workflow files.

This needs PyYAML, so it is a CI-only check rather than part of the stdlib-only
test suite. `tests/run.py` covers the job graph without a parser; this catches
what a parser is actually needed for: indentation errors, malformed `on:` keys,
and expressions that do not look like expressions.

Usage:  python3 scripts/check-workflows.py
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")

# Expressions must be balanced: an unbalanced ${{ is a syntax error at runtime.
UNBALANCED_EXPRESSION = re.compile(r"\$\{\{(?![^}]*\}\})")


def main() -> int:
    try:
        import yaml
    except ModuleNotFoundError:
        print(
            "FAIL PyYAML is not installed; run this in CI or `pip install pyyaml`", file=sys.stderr
        )
        return 1

    if not os.path.isdir(WORKFLOW_DIR):
        print("FAIL no .github/workflows directory", file=sys.stderr)
        return 1

    failures = []
    for name in sorted(os.listdir(WORKFLOW_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(WORKFLOW_DIR, name)
        with open(path) as handle:
            text = handle.read()

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            failures.append(f"{name}: not valid YAML: {exc}")
            continue

        if not isinstance(data, dict):
            failures.append(f"{name}: top level is not a mapping")
            continue

        # YAML 1.1 parses the bare key `on:` as the boolean True, which silently
        # breaks anything that looks for "on".
        if "on" not in data and True not in data:
            failures.append(f"{name}: missing an `on:` trigger block")

        for job_id, job in (data.get("jobs") or {}).items():
            if not isinstance(job, dict):
                failures.append(f"{name}: job {job_id!r} is not a mapping")
                continue
            if "uses" not in job and "runs-on" not in job:
                failures.append(f"{name}: job {job_id!r} has neither uses nor runs-on")

        for match in UNBALANCED_EXPRESSION.finditer(text):
            failures.append(f"{name}: unbalanced ${{{{ at offset {match.start()}")

        print(f"  ok   {name}: {len(data.get('jobs') or {})} jobs")

    if failures:
        for failure in failures:
            print(f"  FAIL {failure}", file=sys.stderr)
        print(f"\n{len(failures)} workflow problem(s)", file=sys.stderr)
        return 1

    print("\nall workflows valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
