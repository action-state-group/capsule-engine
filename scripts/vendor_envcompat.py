# SPDX-License-Identifier: Apache-2.0
"""Re-vendor ``envcompat.py`` from a local capsule-ledger checkout.

``envcompat.env_get`` is a trivial env-var read helper used throughout this
repo (telemetry, cli, console). Its canonical copy lives in capsule-ledger
(``capsule_ledger/envcompat.py``); this repo keeps its own vendored copy
rather than taking a runtime dependency edge on capsule-ledger for a single
helper function. Re-run this script by hand whenever capsule-ledger's
envcompat.py changes; CI's vendor-drift check (``.github/workflows/
vendor-drift.yml``) catches a copy that silently fell out of sync.

Both repos are Apache-2.0 (Action State Group), so vendoring this file is not
a licensing concern -- the vendored file's header records the exact
capsule-ledger commit it came from for provenance.

Usage:
    python scripts/vendor_envcompat.py [path-to-capsule-ledger-checkout]
    python scripts/vendor_envcompat.py --check [path-to-capsule-ledger-checkout]

If no path is given, tries the sibling-checkout convention this workspace
already uses elsewhere (``$LEDGER_REPO_PATH``, else ``../capsule-ledger``
relative to this repo's root).

``--check`` is what CI's vendor-drift workflow runs. It compares the vendored
copy's CODE BODY against the checkout's current source -- never the
provenance header. The header records the exact commit a human vendored
from, so it always differs from whatever commit a fresh capsule-ledger
checkout happens to be at by the time CI runs; diffing the raw bytes (as an
earlier version of this check did) meant vendor-drift failed on every
capsule-ledger commit that touched anything at all, not just on real drift
in envcompat.py itself. ``--check`` still fails loudly the moment the actual
code diverges.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_RELPATH = Path("capsule_ledger") / "envcompat.py"
VENDORED_PATH = REPO_ROOT / "capsule_engine" / "envcompat.py"

HEADER = (
    "# SPDX-License-Identifier: Apache-2.0\n"
    "# Vendored from action-state-group/capsule-ledger, capsule_ledger/envcompat.py,\n"
    "# commit {commit}. Do not hand-edit -- re-run scripts/vendor_envcompat.py\n"
    "# against a capsule-ledger checkout instead.\n"
)


def _find_capsule_ledger(explicit: str | None) -> Path:
    import os

    candidates = [Path(explicit)] if explicit else []
    env = os.environ.get("LEDGER_REPO_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(REPO_ROOT.parent / "capsule-ledger")
    for c in candidates:
        if c and (c / SOURCE_RELPATH).exists():
            return c.resolve()
    raise SystemExit(
        "no capsule-ledger checkout found -- pass a path, set $LEDGER_REPO_PATH, "
        "or place a checkout at ../capsule-ledger next to this repo"
    )


def _current_body(capsule_ledger: Path) -> str:
    source_text = (capsule_ledger / SOURCE_RELPATH).read_text(encoding="utf-8")
    # Drop the source's own SPDX line; the vendored header supplies its own
    # plus provenance, and the two would otherwise appear twice.
    return "\n".join(source_text.splitlines()[1:])


def _vendored_body() -> str:
    header_lines = len(HEADER.splitlines())
    return "\n".join(VENDORED_PATH.read_text(encoding="utf-8").splitlines()[header_lines:])


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--check"]
    check = "--check" in argv[1:]
    capsule_ledger = _find_capsule_ledger(args[0] if args else None)

    if not check:
        # --check compares content only and never records a commit sha, so a
        # dirty checkout doesn't compromise it the way it would a real vendor.
        dirty = subprocess.run(
            ["git", "-C", str(capsule_ledger), "status", "--porcelain", "--", str(SOURCE_RELPATH)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if dirty:
            raise SystemExit(
                f"refusing to vendor from a dirty capsule-ledger checkout ({capsule_ledger}) -- "
                "commit or stash first so the recorded commit sha is meaningful"
            )

    body = _current_body(capsule_ledger)

    if check:
        if not VENDORED_PATH.exists():
            raise SystemExit(f"{VENDORED_PATH} does not exist -- run scripts/vendor_envcompat.py to create it")
        if _vendored_body().rstrip("\n") != body.rstrip("\n"):
            raise SystemExit(
                f"{VENDORED_PATH} is out of sync with {capsule_ledger}/{SOURCE_RELPATH} "
                "-- run scripts/vendor_envcompat.py and commit the result"
            )
        print(f"{VENDORED_PATH} matches {SOURCE_RELPATH} (provenance header not compared)")
        return 0

    commit = subprocess.run(
        ["git", "-C", str(capsule_ledger), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    VENDORED_PATH.parent.mkdir(parents=True, exist_ok=True)
    VENDORED_PATH.write_text(HEADER.format(commit=commit) + body + "\n", encoding="utf-8")
    print(f"wrote {VENDORED_PATH} (vendored from capsule-ledger@{commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
