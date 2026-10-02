#!/usr/bin/env python3
"""Derive the next version from conventional commits and write it.

Stdlib only, so the release workflow needs no dependency install to compute a
version. Usage:

    python3 scripts/bump_version.py --print   # print the next version
    python3 scripts/bump_version.py --notes   # print its release notes

There is nothing to write: the tag is the version. See
``ebay_deals.resolve_version``.

Rules (semantic versioning, conventional commits):

* breaking change in the subject or a ``BREAKING CHANGE:`` footer -> major
* ``feat:`` -> minor
* ``fix:``, ``perf:``, ``refactor:``, ``test:``, ``docs:``, ``ci:``, ``chore:``,
  ``build:``, ``style:`` -> patch
* only unrecognised commits -> no release

``0.x`` versions never bump major, because 0.x is where breaking changes are
allowed to land.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?(?P<breaking>!)?:\s+(?P<subject>.+)$")
PATCH_TYPES = {"fix", "perf", "refactor", "test", "docs", "ci", "chore", "build", "style", "revert"}

# The version lives in exactly one place: the tag. Nothing on main carries it,
# because main is PR-only and a bot commit there would be blocked.
# Used only before the first tag exists.
INITIAL_VERSION = "0.1.0"


def current_version() -> str:
    """The newest version tag, or the release floor on a fresh repo."""
    tag = last_tag()
    return tag.lstrip("v") if tag else INITIAL_VERSION


def commits_since(tag: str | None) -> list[str]:
    rng = f"{tag}..HEAD" if tag else "HEAD"
    # NUL-separated records: a commit body contains blank lines of its own, so
    # splitting `%B` on newlines runs one commit's body into the next commit's
    # subject and every body line lands in the release notes.
    out = subprocess.run(
        ["git", "log", "--no-merges", "--pretty=format:%B%x00", rng],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [record.strip() for record in out.split("\0") if record.strip()]


def last_tag() -> str | None:
    out = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT, capture_output=True, text=True
    )
    tag = out.stdout.strip()
    if not tag:
        return None
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    return tag if match else None


def subject_of(message: str) -> str:
    """First line of a commit message."""
    return message.strip().splitlines()[0] if message.strip() else ""


def next_version(version: str, messages: list[str]) -> str | None:
    """Return the bumped version, or None when nothing warrants a release."""
    match = VERSION_RE.match(version)
    if not match:
        raise SystemExit(f"current version {version!r} is not semver")
    major, minor, patch = (int(part) for part in match.groups())

    # Keep the highest-priority bump seen so far.
    priority = {"patch": 1, "minor": 2, "major": 3}
    bump = None
    for message in messages:
        subject = subject_of(message)
        parsed = CONVENTIONAL.match(subject)
        breaking = "BREAKING CHANGE:" in message or (
            parsed is not None and parsed.group("breaking")
        )
        if breaking:
            # Nothing outranks a breaking change.
            bump = "major"
            break
        if parsed is None:
            continue
        kind = parsed.group("type")
        candidate = "minor" if kind == "feat" else "patch" if kind in PATCH_TYPES else None
        if candidate and (bump is None or priority[candidate] > priority[bump]):
            bump = candidate
    if bump is None:
        return None

    if bump == "major":
        # 0.x: breaking changes go to the minor slot, per semver.
        return (
            "0.1.0"
            if (major, minor, patch) == (0, 0, 0)
            else (f"{major + 1}.0.0" if major else f"0.{minor + 1}.0")
        )
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def release_notes(messages: list[str], version: str) -> str:
    buckets: dict[str, list[str]] = {
        "Breaking changes": [],
        "Features": [],
        "Fixes": [],
        "Performance": [],
        "Documentation": [],
        "Tests": [],
        "Chores": [],
    }
    label = {
        "feat": "Features",
        "fix": "Fixes",
        "perf": "Performance",
        "docs": "Documentation",
        "test": "Tests",
    }
    for message in messages:
        subject = subject_of(message)
        parsed = CONVENTIONAL.match(subject)
        breaking = "BREAKING CHANGE:" in message or (
            parsed is not None and parsed.group("breaking")
        )
        if breaking:
            buckets["Breaking changes"].append(subject)
            detail = message.split("BREAKING CHANGE:", 1)[-1].strip()
            if detail and detail != subject:
                buckets["Breaking changes"].append(f"  {detail}")
            continue
        if parsed is None:
            buckets["Chores"].append(subject)
            continue
        buckets[label.get(parsed.group("type"), "Chores")].append(subject)

    notes = [f"## v{version}", ""]
    for heading, entries in buckets.items():
        if entries:
            notes += [f"### {heading}", ""] + [f"- {entry}" for entry in entries] + [""]
    return "\n".join(notes).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print",
        dest="show",
        action="store_true",
        help="print the next version without writing anything",
    )
    parser.add_argument(
        "--notes", action="store_true", help="print release notes for the next version"
    )
    parser.add_argument(
        "--force-patch",
        action="store_true",
        help="release a patch even when no commit warrants it",
    )
    args = parser.parse_args(argv)
    if not (args.show or args.notes):
        args.show = True

    version = current_version()
    messages = commits_since(last_tag())
    target = next_version(version, messages)

    if target is None and args.force_patch:
        major, minor, patch = (int(part) for part in version.split("."))
        target = f"{major}.{minor}.{patch + 1}"

    if target is None:
        # Nothing releasable. Explain on stderr and print no version, so a
        # caller doing `--print` gets an empty string rather than prose it
        # would try to parse.
        print(f"no releasable commits since the last tag; staying at {version}", file=sys.stderr)
        return 0

    if args.notes:
        print(release_notes(messages, target), end="")
    if args.show:
        print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
