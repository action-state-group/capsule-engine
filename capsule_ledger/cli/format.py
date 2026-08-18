# SPDX-License-Identifier: Apache-2.0
"""Shared CLI output discipline: the DM Mono fold-envelope line, staleness
copy, the CLI-echo canonical form, and one capsule-summary helper.

The envelope-line format is copied verbatim from the design handoff
README's "Product laws" section (`design_handoff_capsule_ledger_ux/README.md`):
``fold <id> · records N–M · checkpoint #X · as of <staleness>`` -- literal
text output, not a font choice. ``<id>`` is the fold's own ``fold`` envelope
field (the definition digest already computed by the fold engine), never a
name this module invents.
"""
from __future__ import annotations

import json
import shlex

from ..payload_store import ResolvedPayload
from ..registry import describe_action_class

__all__ = [
    "format_staleness",
    "format_envelope_line",
    "build_echo",
    "summarize_action",
    "format_action_class",
    "assurance_grade_parts",
    "format_assurance_grade",
    "format_resolved_payload",
]


def format_staleness(age_ms: int) -> str:
    """Render a checkpoint age in the design's "as of 14s ago" style.

    ``age_ms <= 0`` (a value freshly computed by this same CLI invocation,
    never backdated) renders as "just now" rather than "0s ago".
    """
    if age_ms <= 0:
        return "just now"
    seconds = age_ms / 1000
    if seconds < 60:
        return f"{round(seconds)}s ago"
    minutes = seconds / 60
    if minutes < 60:
        return f"{round(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)}h ago"
    days = hours / 24
    return f"{round(days)}d ago"


def format_envelope_line(envelope: dict) -> str:
    """Render a fold result envelope (spec §4 shape) as the literal DM Mono line."""
    r0, r1 = envelope["range"]
    checkpoint_num = envelope["checkpoint"].get("tree_size")
    staleness_ms = envelope["staleness"].get("checkpoint_age_ms", 0)
    return (
        f"fold {envelope['fold']} · records {r0}–{r1} · "
        f"checkpoint #{checkpoint_num} · as of {format_staleness(staleness_ms)}"
    )


def build_echo(verb: str, *, positional: str | None = None, flags: list[tuple[str, object]] = ()) -> str:
    """The CLI-echo canonical form: the exact, copy-pasteable invocation that
    produced this output (design handoff README, "CLI echo standing rule").
    Flag order is fixed (whatever the caller passes), not the order the user
    typed them in, so the same query always echoes identically.
    """
    tokens = ["capsule", verb]
    if positional is not None:
        tokens.append(shlex.quote(str(positional)))
    for flag, value in flags:
        if value is None or value is False:
            continue
        tokens.append(flag)
        if value is not True:
            tokens.append(shlex.quote(str(value)))
    return "≡ " + " ".join(tokens)


def summarize_action(capsule: dict) -> str:
    """The verb portion of ``action_id`` (e.g. "approve_purchase"), falling
    back to ``action_type`` for records with no ``action_id``."""
    action_id = capsule.get("action_id") or ""
    verb = action_id.split("/", 1)[0] if action_id else ""
    return verb or capsule.get("action_type") or "(unnamed action)"


def format_action_class(capsule: dict) -> str | None:
    """Convention-label line for this capsule's ``asg_payload.action_class``
    (design principle item 2) -- ``None`` when the capsule carries no
    ``action_class`` at all (a legitimately different state from an
    unregistered *value*, which renders instead of being hidden)."""
    action_class = (capsule.get("asg_payload") or {}).get("action_class")
    convention = describe_action_class(action_class)
    if convention.action_class is None:
        return None
    if convention.registered:
        return f"{convention.action_class} — {convention.label}"
    return f"{convention.action_class} (unregistered)"


_ATTESTATION_LABELS = {"self_attested": "self-attested", "anchored": "anchored"}


def assurance_grade_parts(assurance: dict) -> tuple[str, bool]:
    """(label, badged) for this assurance block -- the one place that
    decides which attestation modes are "badged" (design principle item 3).
    Extensible to a future ``countersigned`` value the same way, once that
    assurance mode is a real field this codebase produces; ``badged`` is
    never about paid/free, only about attestation strength."""
    mode = assurance.get("attestation_mode")
    label = _ATTESTATION_LABELS.get(mode, mode or "(none)")
    ledger_mode = assurance.get("ledger_mode")
    grade = f"{label} · ledger: {ledger_mode}" if ledger_mode else label
    return grade, mode == "anchored"


def format_assurance_grade(assurance: dict) -> str:
    """Assurance grades do the honesty work everywhere (design principle
    item 3): ``self_attested`` renders plain, ``anchored`` is badged
    (``[...]``) -- driven only by the real ``attestation_mode``/
    ``ledger_mode`` values on the record, never invented, never upsell
    copy."""
    grade, badged = assurance_grade_parts(assurance)
    return f"[{grade}]" if badged else grade


def format_resolved_payload(label: str, resolved: ResolvedPayload, *, indent: str = "      ") -> list[str]:
    """Render one resolve-at-read result (item 5): matched content is shown
    beside the commitment with an explicit "not part of the record"
    marking; a mismatch is a loud failure line, never a silent fallback to
    the digest-only view."""
    if resolved.match:
        lines = [
            f"{indent}resolved {label} "
            "(from your local payload store — not part of the record; digest recomputed live: match):"
        ]
        lines.extend(f"{indent}  {line}" for line in json.dumps(resolved.content, indent=2, sort_keys=True).splitlines())
        return lines
    return [
        f"{indent}⚠ resolved {label} found in your local payload store but does NOT match — "
        f"recomputed {resolved.recomputed_digest} ≠ recorded {resolved.digest} "
        "(the local copy may be corrupted or tampered; treat this content as unverified)"
    ]
