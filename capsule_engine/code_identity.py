# SPDX-License-Identifier: Apache-2.0
"""What code produced a guard decision, captured when the check runs.

``code_identity`` is a LOCAL, INTERIM field, pending a field in the Agent
Action Capsule evidence-result schema [recomputed-tier-code-identity]. When
that field lands, this one is renamed onto it, never kept beside it. It is
the same field the everyday and seller gold labels carry
(``tests/test_pack_everyday_gold_labels.py``): ``{kind, value, assurance}``,
plus what was observed while resolving it.

It is captured by ``capture()`` each time ``GuardEngine.check`` runs -- never
at import or install time. An editable install (``pip install -e .``) runs
whatever source is on disk now, so an identity recorded earlier would name
code that may no longer be the code running. (The git calls and the digest
are re-run only when a file under ``capsule_engine/`` or the git state has
changed since the last capture; ``capture`` reads that every time.)

Resolution, in order:

1. The package is the ``capsule_engine/`` directory at the top of a git work
   tree. ``git rev-parse HEAD`` and ``git status`` run now, scoped to that
   directory. Clean: ``kind`` ``git_commit``, ``value`` the commit,
   ``dirty`` false. Dirty (a tracked change or an untracked file under
   ``capsule_engine/``): the commit no longer names the code, so ``kind`` is
   ``source_tree_sha256`` over the files on disk now, ``dirty`` true, and
   ``base_commit`` the commit the edits sit on.
2. Anything else (an installed wheel, a source copy with no git):
   ``kind`` ``source_tree_sha256`` over the installed files, computed now.
   A wheel built by the release workflow also carries ``build_commit``, the
   commit the release workflow stamped into ``capsule_engine/_build_commit``
   at build time; it is reported beside the digest, never in place of it.
3. Nothing resolvable: ``value`` ``"unknown"``. A decision never fails
   because its code identity could not be read.

``source_tree_sha256`` is SHA-256 over the sorted
``"<path>\\0<sha256 of file>\\n"`` lines of the verdict-producing source and
data tables (``CODE_SCOPE`` and ``DATA_TABLES``), paths relative to the
directory holding ``capsule_engine/`` -- so an editable checkout and an
installed wheel of the same source give the same digest.

``assurance`` is a level from the existing ladder (self-attested | witnessed
| countersigned) and is ``self-attested``: this process reports on itself,
and nothing outside it has checked the report. It makes decisions
COMPARABLE -- the same code across a window -- not CHECKABLE: a stranger
cannot confirm it from the decision. A version string is never an identity
(``__version__`` has not moved since 0.0.1).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

__all__ = [
    "ASSURANCE",
    "ASSURANCE_LEVELS",
    "CODE_SCOPE",
    "DATA_TABLES",
    "KINDS",
    "capture",
    "source_tree_sha256",
]

# The source that produces a guard verdict: the checks and engine, the fold
# engine caps reads, and the pack/manifest path that configures them.
CODE_SCOPE = ("capsule_engine/guards", "capsule_engine/folds", "capsule_engine/policy", "capsule_engine/packs")
# The data tables the guards read a verdict from (today the action taxonomy:
# a class's approver role decides ask versus refuse), hashed with the source.
DATA_TABLES = "capsule_engine/guards/*.json"

ASSURANCE_LEVELS = frozenset({"self-attested", "witnessed", "countersigned"})
# What this process can say about itself. Raised only by an attestation path
# that does not exist yet (README, "Releases and code identity").
ASSURANCE = "self-attested"
KINDS = frozenset({"source_tree_sha256", "git_commit"})

PACKAGE = "capsule_engine"
BUILD_STAMP = "_build_commit"
# The directory holding ``capsule_engine/``: a checkout's top level, or
# site-packages. Read at every capture, so a test may point it elsewhere.
_ROOT = Path(__file__).resolve().parent.parent

# Per root: the git directories (located once) and the last capture with
# the fingerprint it was computed under.
_GIT_DIRS: dict[str, tuple[Path, Path] | None] = {}
_LAST: dict[str, tuple[tuple, dict]] = {}
_RACY_NS = 2_000_000_000

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
# A caller's git environment (a hook, a CI step) must not redirect which
# repository is read.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")


def source_tree_sha256(root: Path | None = None) -> str:
    """SHA-256 over the verdict-producing source and data tables under
    ``root`` (the directory holding ``capsule_engine/``), read now."""
    root = _ROOT if root is None else root
    paths = [*(p for scope in CODE_SCOPE for p in (root / scope).rglob("*.py")), *root.glob(DATA_TABLES)]
    lines = [f"{p.relative_to(root).as_posix()}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n" for p in paths]
    return hashlib.sha256("".join(sorted(lines)).encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> str | None:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    try:
        done = subprocess.run(
            ["git", "-C", str(root / PACKAGE), *args],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _git_state(root: Path) -> tuple[str, bool] | None:
    """(HEAD, dirty) when ``root/capsule_engine`` is the package directory at
    the top of a git work tree; else ``None`` (e.g. site-packages inside
    some other repository's ignored ``.venv``)."""
    out = _git(root, "rev-parse", "--show-prefix", "HEAD")
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) != 2 or lines[0] != f"{PACKAGE}/" or not _HEX40.match(lines[1]):
        return None
    status = _git(root, "status", "--porcelain", "--untracked-files=all", "--", ".")
    if status is None:
        return None
    return lines[1], bool(status.strip())


def _build_commit(root: Path) -> str | None:
    try:
        value = (root / PACKAGE / BUILD_STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if _HEX40.match(value) else None


def _tree_or_unknown(root: Path) -> str:
    if not (root / PACKAGE).is_dir():
        return "unknown"
    try:
        return source_tree_sha256(root)
    except OSError:
        return "unknown"


def _git_dirs(root: Path) -> tuple[Path, Path] | None:
    """(git dir, common dir) of the work tree holding ``root/capsule_engine``.
    Where a repository lives does not change while a process runs, so this
    is looked up once per root; what is IN it is read at every capture."""
    key = str(root)
    if key not in _GIT_DIRS:
        out = _git(root, "rev-parse", "--absolute-git-dir", "--git-common-dir")
        lines = out.splitlines() if out is not None else []
        if len(lines) == 2:
            git_dir = Path(lines[0])
            common = Path(lines[1])
            _GIT_DIRS[key] = (git_dir, common if common.is_absolute() else (root / PACKAGE / common).resolve())
        else:
            _GIT_DIRS[key] = None
    return _GIT_DIRS[key]


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _fingerprint(root: Path) -> tuple:
    """Size and mtime of every file under ``root/capsule_engine`` and of the
    git state that names HEAD and decides dirtiness. Equal fingerprints mean
    nothing a capture reads has changed, so the previous result stands; any
    edit, commit, checkout or reset changes it."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root / PACKAGE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        files.extend((dirpath, name, _stat(Path(dirpath, name))) for name in filenames)
    git: tuple = ()
    dirs = _git_dirs(root)
    if dirs is not None:
        git_dir, common = dirs
        try:
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            head = None
        ref = common / head[5:].strip() if head and head.startswith("ref:") else None
        git = (head, ref and _stat(ref), _stat(common / "packed-refs"), _stat(git_dir / "index"))
    return tuple(sorted(files)), git


def capture(root: Path | None = None) -> dict:
    """The code identity of ``capsule_engine`` as it is on disk now. Called
    per check. The disk is read every time; the git calls and the digest are
    recomputed whenever any file or git state they depend on has changed
    (``_fingerprint``), and otherwise the last result for this root stands."""
    root = _ROOT if root is None else root
    fingerprint = _fingerprint(root)
    cached = _LAST.get(str(root))
    if cached is not None and cached[0] == fingerprint:
        return dict(cached[1])
    identity = _resolve(root)
    # Racy-file rule, as git's index has it: a file modified within the
    # timestamp granularity of now could change again without its stat
    # changing, so a result read from it is not reused.
    if time.time_ns() - _newest_mtime_ns(fingerprint) > _RACY_NS:
        _LAST[str(root)] = (fingerprint, identity)
    return dict(identity)


def _newest_mtime_ns(fingerprint: tuple) -> int:
    files, git = fingerprint
    stats = [st for *_, st in files if st is not None]
    stats += [st for st in git[1:] if isinstance(st, tuple)] if git else []
    return max((mtime for mtime, _ in stats), default=0)


def _resolve(root: Path) -> dict:
    state = _git_state(root)
    if state is not None:
        head, dirty = state
        if not dirty:
            return {"kind": "git_commit", "value": head, "assurance": ASSURANCE, "dirty": False}
        return {"kind": "source_tree_sha256", "value": _tree_or_unknown(root), "assurance": ASSURANCE,
                "dirty": True, "base_commit": head}
    identity = {"kind": "source_tree_sha256", "value": _tree_or_unknown(root), "assurance": ASSURANCE}
    stamped = _build_commit(root)
    if stamped is not None:
        identity["build_commit"] = stamped
    return identity
