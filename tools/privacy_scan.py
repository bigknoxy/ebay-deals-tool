"""Privacy gate for a public repository.

This package is deliberately generic: it must not carry a specific machine's
model, part numbers, shipping ZIP, home directory, or developer credentials.
Everything is checked with precise patterns rather than loose keyword greps,
because a scanner that cries wolf gets disabled, and one that stays quiet is
worse than none.

Run:  python3 -m tools.privacy_scan [--include-untracked]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files that describe the gate itself are exempt from the patterns they contain,
# otherwise the scanner flags its own pattern list.
SELF_REFERENTIAL = ("tools/privacy_scan.py", "scripts/privacy-scan.sh")

# Text-ish files worth scanning. Binary and vendored trees are skipped.
TEXT_SUFFIXES = {
    ".py",
    ".sh",
    ".yml",
    ".yaml",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".cfg",
    ".ini",
    ".example",
    ".gitignore",
    ".editorconfig",
    "",
}
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

# --- credential shapes ------------------------------------------------------
# eBay App ID: 5 dash groups, 40 chars, mixed case and digits.
# Client secret: opaque, >=32 chars, mixed case and digits, no dashes needed.
# Both are constrained to require BOTH a digit and a lowercase letter adjacent
# to uppercase, which is what separates them from English prose.
# The digit/case lookaheads must skip over the SAME character class they
# require, not its complement: `[^A-Za-z0-9]*[A-Z]` cannot match a token that
# consists entirely of class members.
EBAY_APP_ID = re.compile(
    r"\b(?=[A-Za-z0-9-]{40}\b)"
    r"(?=[A-Za-z0-9-]*[A-Z])(?=[A-Za-z0-9-]*[a-z])(?=[A-Za-z0-9-]*[0-9])"
    r"[A-Za-z0-9]{8}-[A-Za-z0-9]{8}-[A-Za-z0-9]{3,5}-[A-Za-z0-9]{6,12}-[A-Za-z0-9]{6,10}\b"
)
OPAQUE_SECRET = re.compile(
    r"(?<![A-Za-z0-9+/=_-])"
    r"(?=[A-Za-z0-9+/=_-]{32,})"
    r"(?=[A-Za-z0-9+/=_-]*[A-Z])(?=[A-Za-z0-9+/=_-]*[a-z])(?=[A-Za-z0-9+/=_-]*[0-9])"
    r"[A-Za-z0-9+/=_-]{32,}"
    r"(?![A-Za-z0-9+/=_-])"
)
AWS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")
SLACK_TOKEN = re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(client_secret|secret|password|passwd|api[_-]?key|access[_-]?token|"
    r"private[_-]?key)\b\s*[:=]\s*[\"']?(?P<value>[^\s\"']{12,})"
)
# A secret assignment is only a finding when the value is not an obvious
# placeholder or environment reference.
PLACEHOLDER = re.compile(
    r"(?i)^(x{3,}|\.{3,}|\*{3,}|none|null|true|false|empty|changeme|your[-_]?"
    r"|placeholder|example|redacted|dummy|test|fixme|os\.environ|process\.env|"
    r"\\$\\{|%s|<[^>]*>|\$\()"
)
# Human-readable, all-lowercase test doubles ("super-secret-value") read as
# words. Real generated secrets are high-entropy and mix case and digits.
READABLE_TEST_DOUBLE = re.compile(r"^[a-z]+([-_][a-z0-9]+)*$")

# --- identity and machine shapes -------------------------------------------
HOME_PATH = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/")
# A ZIP only matters in an address-like context: a street word, or "ZIP"/"postal"
# on the same line. Bare 5-digit constants (timeouts, limits) must not match.
ZIP_IN_ADDRESS = re.compile(
    r"(?i)\b(street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd\.?|lane|ln\.?|"
    r"drive|dr\.?|court|ct\.?|way|suite|ste\.?|apt\.?|zip|postal|"
    r"shipping address|ship to)\b[^\n]{0,60}\b\d{5}(?:-\d{4})?\b"
)
MACHINE_MODEL = re.compile(
    r"(?i)\b(20N20046|T490|T14[0-9]|T5[68]10|T3600|T1700|Z[246]40|KCP4[0-9]{3}|"
    r"M471A[0-9A-Z]+|MTA1[0-9A-Z]+-2G|HMA[0-9]{2}[A-Z]|KVR[0-9]{2}S[0-9]|"
    r"CT[0-9]{2}G4S[A-Z0-9]+)\b"
)
# The owning account appears unavoidably in badge URLs, project URLs, and the
# org-wide reusable workflow reference. Those are public repo metadata, not a
# leak. A personal handle mentioned in prose is a finding.
ACCOUNT_URL_REF = re.compile(
    r"(?i)(img\.shields\.io/(?:github|badge)/\S*"
    r"|github\.com/[a-z0-9-]+/\S*"
    r"|[a-z0-9-]+/\.github(?:/\.github)?/workflows?/[a-z-]*\.?ya?ml"
    r"|\$GITHUB_REPOSITORY)"
)
DEVELOPER_IDENTITY = re.compile(r"(?i)\b(bigknoxy|joshify)\b")
GITHUB_ACCOUNT_HINT = re.compile(r"(?i)github\.com/[a-z0-9-]+/")

CHECKS = (
    ("eBay App ID", EBAY_APP_ID),
    ("opaque secret", OPAQUE_SECRET),
    ("AWS access key id", AWS_KEY_ID),
    ("GitHub token", GITHUB_TOKEN),
    ("Slack token", SLACK_TOKEN),
    ("private key block", PRIVATE_KEY),
    ("secret assignment", SECRET_ASSIGNMENT),
    ("personal home path", HOME_PATH),
    ("shipping ZIP in address", ZIP_IN_ADDRESS),
    ("machine model", MACHINE_MODEL),
    ("developer identity", DEVELOPER_IDENTITY),
)


def iter_files(include_untracked: bool = False) -> list[str]:
    """Tracked files, relative to the repo root. Untracked local state is skipped."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        out = ""
    files = [line.strip() for line in out.splitlines() if line.strip()]
    if include_untracked:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
                if rel not in files:
                    files.append(rel)
    return sorted(files)


def is_scannable(path: str) -> bool:
    if any(part in SKIP_DIRS for part in path.split(os.sep)):
        return False
    name = os.path.basename(path)
    if name == ".gitignore" or name.startswith("."):
        return True
    return os.path.splitext(name)[1].lower() in TEXT_SUFFIXES


def scan_text(path: str, text: str) -> list[tuple[str, int, str]]:
    findings = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            line = line[:4000]
        for label, pattern in CHECKS:
            for match in pattern.finditer(line):
                if label == "secret assignment":
                    value = match.group("value")
                    if (
                        PLACEHOLDER.match(value)
                        or value.endswith("()")
                        or READABLE_TEST_DOUBLE.match(value)
                    ):
                        continue
                    snippet = f"{match.group(1)} = <redacted>"
                elif label == "developer identity":
                    # The account owning the repo is public metadata.
                    if ACCOUNT_URL_REF.search(line):
                        continue
                    snippet = "<redacted>"
                else:
                    # Never echo the matched text: it may be a live credential.
                    snippet = "<redacted>"
                findings.append((label, line_no, snippet))
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-untracked", action="store_true", help="also scan files git does not track"
    )
    args = parser.parse_args(argv)

    files = [f for f in iter_files(args.include_untracked) if is_scannable(f)]
    total_findings = 0
    for rel in files:
        if rel.replace(os.sep, "/") in SELF_REFERENTIAL:
            continue
        path = os.path.join(ROOT, rel)
        try:
            with open(path, errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        findings = scan_text(rel, text)
        if not findings:
            continue
        print(f"FAIL {rel}", file=sys.stderr)
        for label, line_no, snippet in findings:
            print(f"  {rel}:{line_no}: {label}: {snippet}", file=sys.stderr)
        total_findings += len(findings)

    if total_findings:
        print(
            f"\nprivacy scan FAILED: {total_findings} finding(s) in {len(files)} files",
            file=sys.stderr,
        )
        print(
            "remove them from tracked files, and rewrite history if they were pushed",
            file=sys.stderr,
        )
        return 1
    print(f"privacy scan clean across {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
