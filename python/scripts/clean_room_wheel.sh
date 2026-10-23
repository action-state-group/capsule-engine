#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build a capsule-engine wheel, install it into a fresh venv, and run
# scripts/clean_room_wheel_check.py against the installed package from a
# directory outside the checkout -- so nothing is importable from source.
#
#   scripts/clean_room_wheel.sh [extra pip install args...]
#
# PYTHON selects the interpreter that builds the wheel and seeds the venv
# (default python3). Extra args go to the venv's `pip install`, after the
# wheel, e.g. a git pin for a dependency not yet released to PyPI.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
python="${PYTHON:-python3}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Build from a clean export of the working tree (tracked + non-ignored files),
# never the checkout itself: setuptools reuses an in-tree build/ and
# *.egg-info/SOURCES.txt from an earlier build, which can put files in the
# wheel that package-data no longer declares.
mkdir "$work/src"
git -C "$repo" ls-files -z --cached --others --exclude-standard \
  | COPYFILE_DISABLE=1 tar -C "$repo" --null -T - -cf - | tar -xf - -C "$work/src"
"$python" -m pip wheel --no-deps --wheel-dir "$work/dist" "$work/src"
wheel="$(ls "$work"/dist/capsule_engine-*.whl)"
"$python" -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --quiet "$wheel" "$@"

cd "$work"
# The installed command runs, and reports the wheel's own version.
want="$("$work/venv/bin/python" -c 'from importlib.metadata import version; print(version("capsule-engine"))')"
"$work/venv/bin/capsule-engine" --help >/dev/null
test "$("$work/venv/bin/capsule-engine" --version)" = "$want"
# The capsulectl plugin answers its handshake, and checks with the schemas the
# wheel carries.
"$work/venv/bin/capsulectl-engine" cli-plugin-metadata | grep -q '"plugin_api": "cli-plugin/v1"'
"$work/venv/bin/capsulectl-engine" check --ruleset "$repo/../examples/rulesets/everyday-history-coverage.yaml" \
  <"$repo/../go/guard/testdata/real-fyi-act/purchase-commit-repeat.input.json" | grep -q '"tier":"recomputed"'
"$work/venv/bin/python" "$repo/scripts/clean_room_wheel_check.py" "$repo/../examples/contracts"
