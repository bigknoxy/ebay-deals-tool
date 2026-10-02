#!/bin/sh
# Assert the package is dependency-free.
#
# The tool ships standard library only, so that `pipx install` works on a bare
# interpreter and the MCP server needs no vendor SDK. This is the packaging-time
# guarantee: pyproject declares no runtime dependencies, every import resolves
# to the stdlib or to this project, and the package imports with no site-packages
# present.
#
# Usage:  sh scripts/no-third-party.sh
set -eu

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"

echo "== declared runtime dependencies =="
"$PY" - <<'EOF'
import sys
import tomllib

with open("pyproject.toml", "rb") as handle:
    project = tomllib.load(handle)["project"]

declared = project.get("dependencies", [])
stdlib = set(sys.stdlib_module_names)
offending = []
for spec in declared:
    name = spec.split(";")[0].strip()
    for operator in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
        name = name.split(operator)[0]
    name = name.strip()
    if name and name not in stdlib:
        offending.append(spec)
if offending:
    print(f"FAIL: non-stdlib runtime dependencies declared: {offending}", file=sys.stderr)
    raise SystemExit(1)
print(f"ok  {len(declared)} runtime dependencies, all stdlib or none")
EOF

echo
echo "== import provenance =="
# -I ignores PYTHONPATH and the user site directory, so anything that still
# resolves came from the standard library or from this checkout.
"$PY" -I - <<'EOF'
import sys

sys.path.insert(0, ".")
import ebay_deals.cli  # noqa: F401
import ebay_deals.mcp_server  # noqa: F401
import ebay_deals.transport  # noqa: F401

third_party = sorted(
    name for name, module in sys.modules.items()
    if getattr(module, "__file__", None)
    and "site-packages" in module.__file__
    and not name.startswith("ebay_deals")
)
if third_party:
    print(f"FAIL: imported third-party modules: {third_party}", file=sys.stderr)
    raise SystemExit(1)
print("ok  no site-packages module was imported")
EOF

echo
echo "no third-party dependencies"
