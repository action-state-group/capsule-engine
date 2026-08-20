# SPDX-License-Identifier: Apache-2.0
"""``capsule setup observe --attach <adapter>``: which of the four onboarding
paths (``docs/onboarding.md``) this observe run is wired to. This module is
deliberately thin -- it names the closed set and states, honestly, which
paths already have wiring in this repo and which do not, rather than
letting ``--attach`` silently accept anything.

Reusing ``docs/onboarding.md``'s own four paths (design §6b: "reusing the
four onboarding paths already in docs/onboarding.md") is what makes
``observe`` "one line per harness": whichever path a developer's harness
already uses to reach this codebase is the same path that feeds ``observe``
its raw event stream -- there is no second integration to write.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ADAPTER_KINDS", "UnknownAdapter", "AdapterInfo", "adapter_info", "describe_adapters"]


@dataclass(frozen=True)
class AdapterInfo:
    kind: str
    onboarding_path: str
    wired: bool
    note: str


# One entry per onboarding path in docs/onboarding.md, in that doc's own
# order. ``wired=True`` means this repo already has runnable code for it
# (a real integration a caller can point ``--input``/a live stream at);
# ``wired=False`` means the path is documented but not yet built -- stated
# here rather than pretended away, same discipline docs/onboarding.md
# itself already uses for paths 2 and 4.
_ADAPTERS: dict[str, AdapterInfo] = {
    "mcp-hook": AdapterInfo(
        kind="mcp-hook",
        onboarding_path="Claude Code: capsule-mcp stdio server + PostToolUse capture hook",
        wired=True,
        note="each hook invocation is one raw dispatch event; see docs/onboarding/claude_code_hook.py",
    ),
    "framework-inprocess": AdapterInfo(
        kind="framework-inprocess",
        onboarding_path="LangGraph/CrewAI: direct in-process call before dispatch",
        wired=True,
        note="wrap the node/tool handler; see docs/onboarding/framework_adapter_example.py",
    ),
    "conversation-log": AdapterInfo(
        kind="conversation-log",
        onboarding_path="offline replay: a JSONL trace of raw events, one per line",
        wired=True,
        note="the format this module's ObserveRecorder consumes directly -- used by capsule setup demo and by this task's own fixtures",
    ),
    "goose-extension": AdapterInfo(
        kind="goose-extension",
        onboarding_path="Goose extension",
        wired=False,
        note="not built -- capsule-mcp is reusable as-is, no Goose-specific adapter exists yet",
    ),
    "dapr-sidecar": AdapterInfo(
        kind="dapr-sidecar",
        onboarding_path="Dapr sidecar",
        wired=False,
        note="not built -- no HTTP surface yet",
    ),
}

ADAPTER_KINDS = frozenset(_ADAPTERS)


class UnknownAdapter(ValueError):
    """``--attach`` named something outside the closed adapter set."""


def adapter_info(kind: str) -> AdapterInfo:
    if kind not in _ADAPTERS:
        raise UnknownAdapter(f"adapter must be one of {sorted(ADAPTER_KINDS)}; got {kind!r}")
    return _ADAPTERS[kind]


def describe_adapters() -> str:
    lines = []
    for info in _ADAPTERS.values():
        status = "wired" if info.wired else "NOT BUILT"
        lines.append(f"  {info.kind:22s} [{status}] {info.onboarding_path} -- {info.note}")
    return "\n".join(lines)
