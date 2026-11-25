#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Local check before a push (asg-agent-team-ethan#409). Runs the commands CI
# runs, with the same flags, and exits non-zero on the first failure.
#
#   scripts/check.sh
#
# It runs every job a pull request ran before asg-agent-team-ethan#409
# (ci.yml, conformance.yml, vendor-drift.yml, the publish-python build),
# including the parts that now run only nightly and on main in
# slow-suites.yml (the conformance vectors and all of pytest; pull requests
# leave out tests/test_generate_go_packs.py) or only on the pull requests
# vendor-drift.yml's path list matches. pytest runs in parallel here, the
# same tests as a serial `pytest -q`. Main and the nightly run nothing beyond
# this, so there is no second tier. vendor-drift clones scitt-payload-binding
# and tau2-bench (shallow, about 450 MB) at their default branches' heads.
# Not run locally: neutrality.yml (its term list is a CI-only secret) and
# dco.yml (it checks a pull request's commits).
#
# Needs Python 3.12 and 3.11 (with uv on PATH, `uv python install` fetches
# them when missing; without uv, python3.12 and python3.11 on PATH), Go at
# go/go.mod's version, rustup (rust/rust-toolchain.toml installs its pin),
# git and network access for the pinned checkouts. Python runs in
# $CHECK_VENV (default .venv), a Python 3.12 venv as CI uses, created and
# installed as CI installs it.
set -euo pipefail

if (($#)); then
  echo "usage: scripts/check.sh" >&2
  exit 2
fi

caller_dir=$PWD
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/capsule-engine-check"
mkdir -p "$cache"

step() { printf '\n== %s\n' "$*"; }

python_for() { # python_for 3.12 -> path of an interpreter of that version
  if command -v uv >/dev/null; then
    uv python install -q "$1" >&2
    uv python find "$1"
  elif command -v "python$1" >/dev/null; then
    command -v "python$1"
  else
    echo "python$1 not found (install it, or install uv)" >&2
    return 1
  fi
}

# The pins CI checks out, read from the workflows so there is one source.
anchor_pin=$(grep -o 'capsule-anchor @ git+[^"]*' .github/workflows/ci.yml)
skills_ref=$(awk '/repository: action-state-group\/evidencebook-skills/{getline; print $2; exit}' \
  .github/workflows/conformance.yml)
[[ -n "$anchor_pin" && -n "$skills_ref" ]] || { echo "could not read the pins from the workflows" >&2; exit 1; }

# clone_at DIR URL [REF]: a cached checkout at REF, or a fresh shallow clone
# of the default branch's head when REF is empty (as actions/checkout does).
clone_at() {
  local dir=$1 url=$2 ref=${3:-}
  if [[ -n "$ref" ]]; then
    [[ -d "$dir/.git" ]] || git clone -q --filter=blob:none "$url" "$dir"
    git -C "$dir" cat-file -e "$ref^{commit}" 2>/dev/null || git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q --detach "$ref"
  else
    rm -rf "$dir"
    git clone -q --depth 1 "$url" "$dir"
  fi
}

venv="${CHECK_VENV:-$repo/.venv}"
# Absolute, read against the caller's directory: the steps below change it.
[[ "$venv" = /* ]] || venv="$caller_dir/$venv"
venv_version() { "$venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])'; }
if [[ ! -x "$venv/bin/python" ]]; then
  step "create $venv (Python 3.12)"
  "$(python_for 3.12)" -m venv "$venv"
elif [[ "$(venv_version)" != 3.12 ]]; then
  echo "$venv has Python $(venv_version); CI tests with 3.12." >&2
  echo "Remove it (rm -rf $venv) to have this script create a 3.12 one, or set CHECK_VENV." >&2
  exit 1
fi
py="$venv/bin/python"
step "install (ci.yml test; conformance packs, pack-sources; publish-python build)"
"$py" -m pip install -q -e "./python[dev]" "$anchor_pin" pyyaml rfc8785 build twine

step "ruff (ci.yml test)"
"$venv/bin/ruff" check --config python/pyproject.toml .
step "pytest, all of it (ci.yml test; slow-suites.yml generate-go-packs-tests)"
(cd python && "$py" -m pytest -q -n auto)
step "release helper tests (ci.yml test)"
(cd python && "$py" -m pytest -q ../.github/test_release.py)
for v in 3.11 3.12; do
  step "clean-room wheel, Python $v (ci.yml clean-room-wheel)"
  (cd python && PYTHON="$(python_for "$v")" bash scripts/clean_room_wheel.sh)
done

step "build and twine check (publish-python.yml build)"
dist="$(mktemp -d)"
trap 'rm -rf "$dist"' EXIT
(cd python && "$py" -m build -q --outdir "$dist" && "$py" -m twine check "$dist"/*)

step "go (conformance.yml go)"
(cd go && export GOWORK=off &&
  { test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }; } &&
  go vet ./... && go test ./...)

step "rust (conformance.yml rust)"
(cd rust && rustup toolchain install && cargo fmt --check &&
  cargo clippy --locked --all-targets -- -D warnings && cargo test --locked)

step "packs (conformance.yml packs)"
"$py" scripts/generate_go_packs.py --check

step "pack-sources (conformance.yml pack-sources), evidencebook-skills at $skills_ref"
clone_at "$cache/evidencebook-skills" https://github.com/action-state-group/evidencebook-skills.git "$skills_ref"
"$py" scripts/check_pack_sources.py "$cache/evidencebook-skills"

step "vectors (slow-suites.yml vectors)"
"$py" scripts/conformance_vectors.py --check
"$py" scripts/referee_vectors.py --check

step "vendor-drift (vendor-drift.yml), upstream default branches"
clone_at "$cache/scitt-payload-binding" https://github.com/action-state-group/scitt-payload-binding.git
"$py" python/scripts/vendor_cpb_registry.py --check "$cache/scitt-payload-binding"
clone_at "$cache/tau2-bench" https://github.com/sierra-research/tau2-bench.git
"$py" python/scripts/vendor_tau2_airline_conversations.py --check \
  --tau2-results-dir "$cache/tau2-bench/data/tau2/results/final"

step "all checks passed"
