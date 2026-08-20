# SPDX-License-Identifier: Apache-2.0
"""Regenerates capsule_engine/examples/data/tau2_airline/*.jsonl.

Not part of the runtime package -- a one-off, reproducible extraction from
two upstream sources, kept in the repo so the vendored data's provenance is
auditable and re-derivable rather than a hand-edited artifact:

1. **tau2-bench's own committed 4-trial airline results** (public,
   https://github.com/sierra-research/tau2-bench, under
   ``data/tau2/results/final/*_airline_*_4trials.json``) -- four files, one
   per model (claude-3-7-sonnet, gpt-4.1, gpt-4.1-mini, o4-mini). This
   script reads trial 0 of all 50 airline tasks from each file and flattens
   every tool-call message pair (the assistant's ``tool_calls`` entry and
   its matching ``role: tool`` response) into one flat JSON-line-per-event
   record, in the same shape record-grounding-bench's own recording
   pipeline uses (``ToolCallEvent``: tool_name/arguments/result_content/
   error/requestor/timestamp, wrapped in span_start/span_end per task) --
   NOT tau2-bench's own message-transcript shape, so one replay module
   (``examples/tau2_airline_reference.py``) can read all five vendored
   datasets uniformly. Only tool-call events are kept; conversational
   assistant/user text is dropped -- this reference is about tool-call
   gating, not dialogue.

2. **record-grounding-bench's pilot-1 run** (2026-08-15, vertex_ai/
   gemini-2.5-flash, live agent, 24-task shift, seed=1) -- already in this
   exact flat shape (it's what record-grounding-bench's own LogRecorder
   writes), copied verbatim from
   ``_work/capsule-vs-logs-benchmark/pilot-1/log.jsonl`` with no
   transformation. This is the one dataset in the set that came from a
   live agent run rather than a replayed third-party transcript.

Run: ``python scripts/vendor_tau2_airline_reference_data.py
--tau2-results-dir <path to tau2-bench's data/tau2/results/final>
--pilot1-log <path to pilot-1's log.jsonl>``

To reproduce the tau2-bench source files from scratch: clone
https://github.com/sierra-research/tau2-bench.git and check out
``data/tau2/results/final/`` -- Git LFS is not required, these are plain
committed JSON in that repo. The exact per-file ``info.git_commit`` each
result set was generated against is recorded in this script's output
PROVENANCE.json, not re-verified here (tau2-bench's own repo state at
generation time, outside this script's control).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "capsule_engine" / "examples" / "data" / "tau2_airline"

MODEL_FILES = {
    "claude-3-7-sonnet": "claude-3-7-sonnet-20250219_airline_default_gpt-4.1-2025-04-14_4trials.json",
    "gpt-4-1": "gpt-4.1-2025-04-14_airline_default_gpt-4.1-2025-04-14_4trials.json",
    "gpt-4-1-mini": "gpt-4.1-mini-2025-04-14_airline_base_gpt-4.1-2025-04-14_4trials.json",
    "o4-mini": "o4-mini-2025-04-16_airline_default_gpt-4.1-2025-04-14_4trials.json",
}

# Exhaustive against airline's tools.py at record-grounding-bench's pinned
# tau2-bench SHA (record_grounding_bench.manifest.rules module docstring) --
# duplicated here as a plain constant since this script deliberately has no
# dependency on record-grounding-bench (see docs/reference/tau2-airline-
# reference.md's "why not import record-grounding-bench" note).
WRITE_TOOLS = {
    "cancel_reservation",
    "send_certificate",
    "book_reservation",
    "update_reservation_baggages",
    "update_reservation_flights",
    "update_reservation_passengers",
}


def _events_for_sim(sim: dict) -> list[dict]:
    calls = {}
    for m in sim["messages"]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                calls[tc["id"]] = (tc["name"], tc.get("arguments") or {}, tc.get("requestor", "assistant"))
    out = []
    for m in sim["messages"]:
        if m.get("role") != "tool":
            continue
        name, args, requestor = calls.get(m["id"], (None, None, None))
        if name is None:
            continue
        content = m.get("content")
        out.append(
            {
                "tool_name": name,
                "arguments": args,
                "result_content": content if isinstance(content, str) else json.dumps(content),
                "error": bool(m.get("error")),
                "requestor": requestor or "assistant",
                "timestamp": m.get("timestamp"),
            }
        )
    return out


def _write_dataset(sims: list[dict], out_path: Path) -> tuple[int, int]:
    n_tasks = 0
    n_events = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for task_seq, sim in enumerate(sims):
            task_id = sim["task_id"]
            trace_id = f"{out_path.stem}-task{task_id}"
            fh.write(json.dumps({"kind": "span_start", "trace_id": trace_id, "task_id": task_id}, separators=(",", ":")) + "\n")
            for call_seq, ev in enumerate(_events_for_sim(sim)):
                ts = ev["timestamp"]
                if ts and not ts.endswith("Z") and "+" not in ts:
                    ts = ts + "Z"  # tau2-bench's own timestamps are naive UTC; normalize to match pilot-1's convention
                record = {
                    "shift_seed": 0,
                    "domain": "airline",
                    "task_id": task_id,
                    "task_seq": task_seq,
                    "call_seq": call_seq,
                    "timestamp": ts,
                    "requestor": ev["requestor"],
                    "tool_name": ev["tool_name"],
                    "tool_type": "write" if ev["tool_name"] in WRITE_TOOLS else "read",
                    "arguments": ev["arguments"],
                    "result_content": ev["result_content"],
                    "error": ev["error"],
                    "latency_ms": None,
                    "kind": "tool_call",
                    "trace_id": trace_id,
                    "span_id": f"{trace_id}-call{call_seq}",
                }
                fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
                n_events += 1
            fh.write(json.dumps({"kind": "span_end", "trace_id": trace_id, "task_id": task_id}, separators=(",", ":")) + "\n")
            n_tasks += 1
    return n_tasks, n_events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tau2-results-dir", required=True, help="tau2-bench's data/tau2/results/final directory")
    parser.add_argument("--pilot1-log", required=True, help="record-grounding-bench pilot-1's log.jsonl")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    results_dir = Path(args.tau2_results_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    provenance: dict[str, dict] = {}
    for model_key, fname in MODEL_FILES.items():
        src = results_dir / fname
        d = json.loads(src.read_text())
        sims = sorted((s for s in d["simulations"] if s["trial"] == 0), key=lambda s: int(s["task_id"]))
        out_path = out_dir / f"tau2_committed_{model_key}_airline_trial0.jsonl"
        n_tasks, n_events = _write_dataset(sims, out_path)
        provenance[model_key] = {
            "source_repo": "https://github.com/sierra-research/tau2-bench",
            "source_file": f"data/tau2/results/final/{fname}",
            "source_git_commit": d["info"].get("git_commit"),
            "trial_used": 0,
            "num_tasks": n_tasks,
            "num_tool_call_events": n_events,
            "output_file": out_path.name,
            "output_bytes": out_path.stat().st_size,
        }
        print(f"{model_key}: {n_tasks} tasks, {n_events} tool-call events -> {out_path}")

    pilot1_src = Path(args.pilot1_log)
    pilot1_out = out_dir / "pilot1_live_gemini-2-5-flash_airline_shift1.jsonl"
    pilot1_out.write_bytes(pilot1_src.read_bytes())
    provenance["pilot1-gemini-2-5-flash"] = {
        "source": "record-grounding-bench pilot-1 (2026-08-15), docs/pilot-1-report.md",
        "record_grounding_bench_pinned_tau2_sha": "668d3bcd135c02aa3438f987ef45735b7c163ee3",
        "model": "vertex_ai/gemini-2.5-flash (both agent and user-simulator roles)",
        "shift_seed": 1,
        "num_tasks": 24,
        "num_tool_call_events": 142,
        "output_file": pilot1_out.name,
        "output_bytes": pilot1_out.stat().st_size,
        "note": "the only dataset here from a LIVE agent run through record-grounding-bench's own "
        "recording pipeline; the other four are replayed from tau2-bench's committed transcripts.",
    }
    print(f"pilot1-gemini-2-5-flash: copied verbatim -> {pilot1_out}")

    (out_dir / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n")
    total = sum(p["output_bytes"] for p in provenance.values())
    print(f"total vendored size: {total} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
