# SPDX-License-Identifier: Apache-2.0
"""Tool-schema snapshot test: pins the exact name/description/inputSchema of
every MCP tool this server exposes, so a future accidental signature change
(a renamed field, a dropped default, a parameter silently added or removed)
is caught by a diff against `tests/fixtures/mcp_tool_schema_snapshot.json`
rather than discovered by a caller at runtime.

Importing `capsule_ledger.mcp.server` and listing its tools requires no ledger
config -- tool registration is pure metadata; nothing here opens a ledger.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from capsule_ledger.mcp.server import mcp

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "mcp_tool_schema_snapshot.json"
EXPECTED_TOOL_NAMES = {
    "ledger_query",
    "fold_list",
    "fold_get",
    "budget_remaining",
    "action_been_done",
    "constraints_list",
    "decision_explain",
    "record_get",
    "record_verify",
    "intent_declare",
}


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs (including the docstring's own newlines
    and indentation) to single spaces. Different `mcp` SDK versions have
    disagreed on whether a tool's docstring gets `inspect.cleandoc`-ed before
    becoming its `description` -- purely cosmetic (this is compared before
    the description even reaches an agent's context), so it must never fail
    this snapshot on its own. A real wording change still shows up: the
    words themselves, not their line-wrapping, are what's pinned."""
    return re.sub(r"\s+", " ", text).strip()


def _normalize_tool(tool: dict) -> dict:
    return {**tool, "description": _normalize_whitespace(tool["description"])}


def _current_schema() -> list[dict]:
    tools = asyncio.run(mcp.list_tools())
    return [
        _normalize_tool({"name": t.name, "description": t.description, "inputSchema": t.inputSchema})
        for t in sorted(tools, key=lambda x: x.name)
    ]


def test_exactly_ten_tools_nine_read_one_write():
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOL_NAMES
    assert len(names) == 10


def test_tool_schema_matches_pinned_snapshot():
    expected = [_normalize_tool(t) for t in json.loads(SNAPSHOT_PATH.read_text())]
    actual = _current_schema()
    assert actual == expected, (
        "MCP tool schema drifted from the pinned snapshot -- if this is an "
        "intentional change, update tests/fixtures/mcp_tool_schema_snapshot.json "
        "to match (and double check any harness config referencing the old shape)."
    )


def test_only_intent_declare_is_a_write_tool():
    """A cheap, targeted mutant check: if this test is ever made to pass by
    deleting the assertion rather than by the code being correct, the read/
    write split it's protecting has silently been lost. Every tool other than
    `intent_declare` must be read-only by construction (no `--dry-run`/write
    escape hatch hiding in its schema)."""
    schema = {t["name"]: t for t in _current_schema()}
    write_tools = {
        name
        for name, tool in schema.items()
        if "WRITE TOOL" in tool["description"] or "only tool that writes" in tool["description"]
    }
    assert write_tools == {"intent_declare"}
