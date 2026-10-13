# SPDX-License-Identifier: Apache-2.0
"""The code identity a guard decision carries: a commit or a source-tree
digest, never a version string, captured when the check runs, assurance
self-attested.

``code_identity`` is a local, interim field, pending a field in the Agent
Action Capsule evidence-result schema [recomputed-tier-code-identity]
(``capsule_engine/code_identity.py``)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from capsule_engine import __version__
from capsule_engine import code_identity as ci
from capsule_engine.guards import Action, GuardEngine

REPO = Path(__file__).resolve().parent.parent
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _source(root: Path, engine_text: str = "ENGINE = 1\n") -> Path:
    guards = root / "capsule_engine" / "guards"
    guards.mkdir(parents=True, exist_ok=True)
    (root / "capsule_engine" / "__init__.py").write_text('__version__ = "0.0.1"\n')
    (guards / "engine.py").write_text(engine_text)
    (guards / "action_taxonomy.json").write_text("{}\n")
    return root


def _repo(root: Path) -> Path:
    _source(root)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "a")
    return root


def _commit(repo: Path, engine_text: str) -> str:
    (repo / "capsule_engine" / "guards" / "engine.py").write_text(engine_text)
    _git(repo, "commit", "-q", "-am", "next")
    return _git(repo, "rev-parse", "HEAD")


def _installed_copy(dest: Path) -> Path:
    """The verdict-producing files laid out as a wheel installs them:
    ``<site-packages>/capsule_engine/...``, no git."""
    for scope in ci.CODE_SCOPE:
        for src in (REPO / scope).rglob("*.py"):
            out = dest / src.relative_to(REPO)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(src.read_bytes())
    for src in REPO.glob(ci.DATA_TABLES):
        (dest / src.relative_to(REPO)).write_bytes(src.read_bytes())
    return dest


# -- two builds from different source report different identities --------------


def test_two_installed_builds_from_different_source_report_different_digests(tmp_path):
    a = ci.capture(_installed_copy(tmp_path / "a"))
    b_root = _installed_copy(tmp_path / "b")
    engine = b_root / "capsule_engine" / "guards" / "engine.py"
    engine.write_text(engine.read_text() + "\n# one changed line\n")
    b = ci.capture(b_root)
    assert a["kind"] == b["kind"] == "source_tree_sha256"
    assert HEX64.match(a["value"]) and HEX64.match(b["value"])
    assert a["value"] != b["value"]


def test_an_installed_build_and_the_checkout_it_came_from_report_the_same_digest(tmp_path):
    assert ci.capture(_installed_copy(tmp_path / "site"))["value"] == ci.source_tree_sha256(REPO)


@needs_git
def test_two_commits_report_different_commits(tmp_path):
    repo = _repo(tmp_path / "repo")
    first = ci.capture(repo)
    second_head = _commit(repo, "ENGINE = 2\n")
    second = ci.capture(repo)
    assert first["kind"] == second["kind"] == "git_commit"
    assert first["dirty"] is second["dirty"] is False
    assert second["value"] == second_head != first["value"]


# -- assurance ------------------------------------------------------------------


def test_every_identity_carries_assurance_self_attested(tmp_path):
    shapes = [ci.capture(_source(tmp_path / "plain")), ci.capture(tmp_path / "nothing-here"), ci.capture()]
    for identity in shapes:
        assert identity["assurance"] == "self-attested"
        assert identity["assurance"] in ci.ASSURANCE_LEVELS
        assert identity["kind"] in ci.KINDS


def test_the_decision_carries_the_identity_with_assurance_self_attested(store, caps_fold, signer):
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer)
    decision = engine.check(Action(verb="look", operator="household", developer="assistant@v1",
                                   action_class="info.query"), dry_run=True)
    identity = decision.code_identity
    assert identity["assurance"] == "self-attested"
    assert identity == ci.capture()
    # Interim and local: carried on the decision, not sealed into the capsule.
    assert "code_identity" not in str(decision.capsule)


def test_a_version_string_is_never_the_identity(tmp_path):
    for identity in (ci.capture(), ci.capture(_source(tmp_path / "plain"))):
        assert identity["value"] != __version__
        assert HEX40.match(identity["value"]) or HEX64.match(identity["value"])


def test_nothing_resolvable_reports_unknown_not_a_guess(tmp_path):
    identity = ci.capture(tmp_path / "empty")
    assert identity == {"kind": "source_tree_sha256", "value": "unknown", "assurance": "self-attested"}


# -- captured at check time -------------------------------------------------------


@needs_git
def test_the_identity_is_captured_at_check_time_not_import_or_engine_build(
    tmp_path, monkeypatch, store, caps_fold, signer
):
    """The module is imported and the engine built while HEAD is one commit;
    HEAD then moves, and an uncommitted edit follows. Each check reports the
    code on disk as that check runs."""
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(ci, "_ROOT", repo)
    first_head = _git(repo, "rev-parse", "HEAD")
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer)
    action = Action(verb="look", operator="household", developer="assistant@v1", action_class="info.query")

    assert engine.check(action, dry_run=True).code_identity["value"] == first_head

    second_head = _commit(repo, "ENGINE = 2\n")
    moved = engine.check(action, dry_run=True).code_identity
    assert moved == {"kind": "git_commit", "value": second_head, "assurance": "self-attested", "dirty": False}

    (repo / "capsule_engine" / "guards" / "engine.py").write_text("ENGINE = 3\n")
    edited = engine.check(action, dry_run=True).code_identity
    assert edited["kind"] == "source_tree_sha256"
    assert edited["dirty"] is True
    assert edited["base_commit"] == second_head
    assert edited["value"] == ci.source_tree_sha256(repo)


@needs_git
def test_an_untracked_source_file_marks_the_tree_dirty(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "capsule_engine" / "guards" / "extra.py").write_text("X = 1\n")
    assert ci.capture(repo)["dirty"] is True


@needs_git
def test_an_edit_outside_the_package_leaves_the_commit_as_the_identity(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "README.md").write_text("notes\n")
    assert ci.capture(repo)["kind"] == "git_commit"


@needs_git
def test_a_package_inside_another_repository_is_not_named_by_that_repository(tmp_path):
    """site-packages under some project's ``.venv`` is inside that project's
    work tree; its commit says nothing about the installed package."""
    outer = tmp_path / "project"
    outer.mkdir()
    (outer / "app.py").write_text("APP = 1\n")
    _git(outer, "init", "-q")
    _git(outer, "add", "-A")
    _git(outer, "commit", "-q", "-m", "a")
    site = _source(outer / ".venv" / "lib" / "site-packages")
    identity = ci.capture(site)
    assert identity["kind"] == "source_tree_sha256"
    assert "dirty" not in identity


def test_a_caller_git_environment_does_not_redirect_the_lookup(tmp_path, monkeypatch):
    expected = ci.capture()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "elsewhere"))
    assert ci.capture() == expected


# -- the release workflow's build stamp ---------------------------------------------


def test_a_stamped_wheel_reports_its_build_commit_beside_the_digest(tmp_path):
    site = _source(tmp_path / "site")
    (site / "capsule_engine" / ci.BUILD_STAMP).write_text("ab" * 20 + "\n")
    identity = ci.capture(site)
    assert identity["kind"] == "source_tree_sha256"
    assert identity["value"] == ci.source_tree_sha256(site)
    assert identity["build_commit"] == "ab" * 20


def test_a_malformed_build_stamp_is_ignored(tmp_path):
    site = _source(tmp_path / "site")
    (site / "capsule_engine" / ci.BUILD_STAMP).write_text("v0.0.1\n")
    assert "build_commit" not in ci.capture(site)


# -- recomputed whenever what it reads changes ----------------------------------------


def test_a_same_size_edit_right_after_a_capture_is_seen(tmp_path):
    """Captures reuse the last result only while every file's size and mtime
    are unchanged and none was modified in the last two seconds, so an edit
    that keeps the size, within the same timestamp tick, still shows."""
    site = _source(tmp_path / "site", "ENGINE = 1\n")
    before = ci.capture(site)
    (site / "capsule_engine" / "guards" / "engine.py").write_text("ENGINE = 2\n")
    assert ci.capture(site)["value"] != before["value"]


def test_an_unchanged_tree_reuses_the_last_result_and_a_change_recomputes(tmp_path, monkeypatch):
    site = _source(tmp_path / "site")
    old = 1_000_000_000_000_000_000  # well outside the racy window
    for path in (site / "capsule_engine").rglob("*"):
        os.utime(path, ns=(old, old))
    calls = []
    resolve = ci._resolve
    monkeypatch.setattr(ci, "_resolve", lambda root: calls.append(root) or resolve(root))
    first = ci.capture(site)
    assert ci.capture(site) == first
    assert len(calls) == 1
    engine = site / "capsule_engine" / "guards" / "engine.py"
    engine.write_text("ENGINE = 22\n")
    os.utime(engine, ns=(old + 1, old + 1))
    assert ci.capture(site)["value"] != first["value"]
    assert len(calls) == 2
