# SPDX-License-Identifier: Apache-2.0
"""Regression for a real, live-found circular import (2026-09-02): importing
``capsule_engine.conversation`` (or ``events``) before ``guards`` had ever
been imported crashed with ``ImportError: cannot import name
'build_event_capsule' from partially initialized module
'capsule_engine.events.capsule'``. Root cause was two-fold -- both fixed
here: ``guards/__init__.py`` re-exported ``build_event_capsule`` from
``events.capsule`` (recreating the exact coupling the W3 split moved it out
to avoid), and ``events/capsule.py`` eagerly imported ``guards.signing.Signer``
for a type annotation only, which is exactly what ``guards/engine.py``
importing ``events.capsule.build_event_capsule`` closes the loop through.

Each test runs in a genuinely FRESH interpreter (``subprocess``), never the
pytest process's own ``sys.modules`` cache -- a previously-successful import
in this same process would mask the bug entirely, since Python caches
modules once loaded (which is exactly why this bug was invisible in the
existing test suite: pytest's own collection order happened to import
``guards`` first)."""
import subprocess
import sys


def _import_in_fresh_interpreter(module: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)


def test_importing_conversation_first_does_not_crash():
    result = _import_in_fresh_interpreter("capsule_engine.conversation.capsules")
    assert result.returncode == 0, result.stderr


def test_importing_events_capsule_first_does_not_crash():
    result = _import_in_fresh_interpreter("capsule_engine.events.capsule")
    assert result.returncode == 0, result.stderr


def test_importing_guards_first_does_not_crash():
    result = _import_in_fresh_interpreter("capsule_engine.guards")
    assert result.returncode == 0, result.stderr


def test_importing_policy_activation_first_does_not_crash():
    """policy/activation.py is another guards-adjacent build_event_capsule
    caller with the same shape of risk -- covered for the same reason."""
    result = _import_in_fresh_interpreter("capsule_engine.policy.activation")
    assert result.returncode == 0, result.stderr
