# SPDX-License-Identifier: Apache-2.0
"""Method-freeze and declared-count checks over a payload-free v2 bundle.

Two record kinds (``action_type`` values) are read, both sealed before the
window opens:

* ``method_freeze`` -- freezes the skill as run (J.3).
* ``declared_count`` -- a count declared before the window.

Only what a withheld bundle shows is read: ``action_type``, ``timestamp``,
``capsule_id`` and ``references[].digest``. Results use the countersign
vocabulary only: ``established`` / ``failed`` / ``not present`` for the
freeze, ``match`` / ``mismatch`` / ``failed`` / ``not present`` for the
count (``mismatch`` only for declared != certified span; a late or duplicate
declaration is ``failed``). Never
pass/fail, never "coverage". ``not checked`` (an existing countersign word)
is used only when a check could not run: no window to compare with, or a
count the holder did not disclose. A check recomputes from the bundle alone, so
anyone can repeat it.

The declared count VALUE is in the withheld payload. Per the ruling on
capsule-engine#160 (Sherlock2's recommendation (b), Buzz thread linked on the
issue), the holder may opt in to disclosing that one record's ``agent_input``
(``disclosures[capsule_id]``). The module then checks the disclosed payload
against the digest sealed in the record, requires the closed shape
``{declared_count, second_system_digest?}``, and compares the count with the
span the completeness certificate proves: ``last_seq - first_seq + 1``, every
record in the certified range. The number a producer declares therefore counts
every record in that range, the ``declared_count`` record and the freeze
included, not only the judgments. With no disclosure the comparison
reads ``not checked``, which is not ``not present`` (no record sealed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agent_action_capsule.bundle import json_digest

METHOD_FREEZE = "method_freeze"
DECLARED_COUNT = "declared_count"
_SETUP_KINDS = frozenset({METHOD_FREEZE, DECLARED_COUNT})

# Public profile ids this module answers to. Registered by the deployer of a
# countersign instance via register_all(); the neutral engine ships none.
PROFILE_IDS = ("asg/outcomes/1", "asg/eu-ai-act-obligations/1")


class DisclosureRefused(ValueError):
    """A disclosed ``declared_count`` payload that must not be read: it does not
    match the digest sealed in its record, or it is not the closed shape. The
    countersign request is refused (never read as a ``mismatch``, which means
    only that the declared count differs from the certified span)."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    result: str
    detail: str = ""


def _records(bundle: Any) -> list[dict]:
    value = bundle.get("records") if isinstance(bundle, dict) else getattr(bundle, "records", None)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _ts(record: dict) -> datetime | None:
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _refs(record: dict) -> set[str]:
    """Capsule ids this record cites, in the shape the bundle graph honors
    (agent_action_capsule.bundle._citation_targets): a typed SHA-256
    ``references[]`` entry or the chain parent."""
    out: set[str] = set()
    chain = record.get("chain")
    if isinstance(chain, dict) and isinstance(chain.get("parent_capsule_id"), str):
        out.add(chain["parent_capsule_id"])
    refs = record.get("references")
    for r in refs if isinstance(refs, list) else []:
        if (
            isinstance(r, dict)
            and r.get("type") == "agent-action-capsule"
            and r.get("digest_alg") == "SHA-256"
            and isinstance(r.get("digest"), str)
        ):
            out.add(r["digest"])
    return out


_COUNT_KEYS = frozenset({"declared_count", "second_system_digest"})


def _raw(bundle: Any) -> dict:
    raw = bundle if isinstance(bundle, dict) else getattr(bundle, "raw", None)
    return raw if isinstance(raw, dict) else {}


def _span(raw: dict) -> int | None:
    cert = raw.get("completeness_certificate")
    if not isinstance(cert, dict):
        return None
    first, last = cert.get("first_seq"), cert.get("last_seq")
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (first, last))
    return last - first + 1 if ints and last >= first else None


def parse_window_start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class CountersignPackModule:
    """``check(bundle, profile_id)``. ``window_start`` is the instant the
    window opens (the request names it; a v2 bundle carries none). When it is
    None the window opens at the earliest record that is neither setup kind."""

    def __init__(self, window_start: datetime | None = None) -> None:
        self.window_start = window_start

    def check(self, bundle: Any, profile_id: str) -> list[CheckResult]:
        records = _records(bundle)
        freezes = [r for r in records if r.get("action_type") == METHOD_FREEZE]
        counts = [r for r in records if r.get("action_type") == DECLARED_COUNT]
        judged = [r for r in records if r.get("action_type") not in _SETUP_KINDS]
        window, window_note = self._window(judged)
        return [
            self._method_freeze(freezes, judged, window, window_note),
            self._declared_count(counts, window, window_note, bundle),
        ]

    def _window(self, judged: list[dict]) -> tuple[datetime | None, str]:
        if self.window_start is not None:
            return self.window_start, "window opens at the requested start"
        stamps = [t for t in (_ts(r) for r in judged) if t is not None]
        if stamps:
            return min(stamps), "window opens at the earliest judged record"
        return None, "no window start given and no judged record to infer one from"

    def _sealed_before(self, record: dict, window: datetime | None) -> tuple[str, bool] | None:
        """None when sealed before the window; else ``(reason, could_not_run)``.
        An unreadable timestamp or a missing window could not run
        (``not checked``); only a readable late timestamp is a failure."""
        if window is None:
            return "no window start to compare against", True
        sealed = _ts(record)
        if sealed is None:
            return "record has no readable timestamp", True
        if sealed >= window:
            return f"sealed {sealed.isoformat()} at or after the window opened {window.isoformat()}", False
        return None

    def _method_freeze(self, freezes, judged, window, note) -> CheckResult:
        name = "method freeze"
        if not freezes:
            return CheckResult(name, "not present", "no method_freeze record in the bundle")
        if len(freezes) > 1:
            return CheckResult(name, "failed", f"{len(freezes)} method_freeze records; the freeze is one record")
        freeze = freezes[0]
        late = self._sealed_before(freeze, window)
        if late is not None:
            return CheckResult(name, "not checked" if late[1] else "failed", late[0])
        if not judged:
            return CheckResult(name, "not checked", "no judgment record in the bundle to reference the freeze")
        digest = freeze.get("capsule_id")
        missing = [r.get("action_id", "?") for r in judged if digest not in _refs(r)]
        if missing:
            return CheckResult(
                name,
                "failed",
                f"{len(missing)} of {len(judged)} judgment record(s) do not reference the freeze digest: "
                + ", ".join(str(m) for m in missing[:5]),
            )
        return CheckResult(
            name,
            "established",
            f"freeze {digest} sealed before the window ({note}); all {len(judged)} judgment record(s) reference it",
        )

    def _declared_count(self, counts, window, note, bundle) -> CheckResult:
        name = "declared count"
        if not counts:
            return CheckResult(name, "not present", "no declared_count record in the bundle")
        if len(counts) > 1:
            return CheckResult(name, "failed", f"{len(counts)} declared_count records; the declaration is one record")
        record = counts[0]
        late = self._sealed_before(record, window)
        if late is not None:
            return CheckResult(name, "not checked" if late[1] else "failed", late[0])
        raw = _raw(bundle)
        disclosed = (raw.get("disclosures") or {}).get(record.get("capsule_id")) if isinstance(raw.get("disclosures"), dict) else None
        payload = disclosed.get("agent_input") if isinstance(disclosed, dict) else None
        if payload is None:
            return CheckResult(
                name,
                "not checked",
                "declared_count is sealed; its value is withheld and the holder disclosed none, "
                "so the comparison with the sequence span is not made",
            )
        sealed = ((record.get("model_attestation") or {}).get("compute_attestation") or {}).get("agent_input_digest")
        try:
            digest = json_digest(payload)
        except (TypeError, ValueError):
            digest = None
        if not isinstance(sealed, str):
            return CheckResult(name, "not checked", "the record commits no agent_input_digest to compare the disclosure with")
        if digest is None or digest != sealed:
            raise DisclosureRefused("the disclosed declared_count payload does not match the digest sealed in the record")
        second = payload.get("second_system_digest") if isinstance(payload, dict) else None
        if second is not None and not (isinstance(second, str) and len(second) == 64 and set(second) <= set("0123456789abcdef")):
            raise DisclosureRefused("second_system_digest is not 64 lowercase hex characters")
        count = payload.get("declared_count") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or not set(payload) <= _COUNT_KEYS or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise DisclosureRefused("the disclosed declared_count payload is not {declared_count, second_system_digest?}")
        span = _span(raw)
        if span is None:
            return CheckResult(name, "not checked", "no completeness certificate proves a sequence span to compare with")
        if count == span:
            return CheckResult(name, "match", f"declared {count}, the certified sequence span is {span}")
        return CheckResult(name, "mismatch", f"declared {count}, the certified sequence span is {span}")


def register_all(registry: Any, module: CountersignPackModule | None = None) -> None:
    """Register the module under every profile id in ``PROFILE_IDS`` on a
    capsule-anchor ``PolicyRegistry`` (``register(profile_id, module)``)."""
    mod = module or CountersignPackModule()
    for pid in PROFILE_IDS:
        registry.register(pid, mod)
