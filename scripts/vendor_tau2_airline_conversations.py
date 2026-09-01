# SPDX-License-Identifier: Apache-2.0
"""Regenerates capsule_engine/examples/data/tau2_airline/tau2_conversations_*.jsonl.

Companion to ``vendor_tau2_airline_reference_data.py``, not a replacement for it.
That script flattens tool-call events only (span_start/tool_call/span_end), which
is everything ``examples/tau2_airline_reference.py``'s guard replay needs, but is
insufficient for ``[ldg-airline-engagement-pack]``'s claims A1/A3/A5/A7 -- those
read the AGENT'S AND USER'S OWN WORDS ("here are three options", "I really need
this now"), which is not present in a tool-call-only extraction. This script
instead vendors the conversation grain: one JSON line per simulation, carrying
the assistant/user message text plus a flattened tool-call-name trail for the
claims (A4, A6) that need only the trail, not the text.

Source: tau2-bench's own committed 4-trial airline results (public,
https://github.com/sierra-research/tau2-bench,
``data/tau2/results/final/<model>_airline_*_4trials.json``). Unlike the
tool-call-only vendoring above (trial 0 only, one file per model), this script
takes ALL FOUR TRIALS from a single model file -- 50 tasks x 4 trials = 200
simulations -- because the airline-engagement-pack task explicitly measures
over "the 200-sim airline file". Default model: claude-3-7-sonnet (the model
already vendored for the tool-call-only reference, so both datasets describe
the same underlying agent).

Per-message ``raw_data``, ``timestamp``, ``turn_idx`` are dropped -- replay-
internal fields the pack's text predicates never read; keeping them would
triple output size for no claim this pack computes. ``role: tool`` messages
(raw tool result payloads) are dropped entirely -- the pack's claims either
read assistant/user prose or the tool-call NAME trail, never a tool result
body.

Inference metadata is RECOVERED (previously dropped). The raw source carries
the "what model, what settings, how much" inference story, and the
``conversation_exchange`` capsule now carries it too (mirroring
``capsule-emit-mesh``'s ``serving_provenance``). Per simulation we vendor:

- ``model`` (``info.agent_info.llm``) -- the AGENT model under test.
- ``generation_parameters`` -- ``temperature`` (``info.agent_info.llm_args``,
  run-level) and ``seed`` (per-``sim`` seed, the actual seed of that run).
  Absent stays absent; nothing is fabricated.
- ``usage`` -- ``prompt_tokens`` / ``completion_tokens`` summed over the
  AGENT's own (``role: assistant``) messages only, plus derived
  ``total_tokens``. The user-simulator is itself a DIFFERENT LLM
  (``gpt-4.1``); folding its tokens into the agent's usage would
  misattribute them, so simulator tokens are excluded.

Per-message ``cost`` (a currency figure) is deliberately NOT vendored:
neutral invariant is meter-not-price -- tokens are the meter a relying party
audits over; currency is derived downstream and stays out of the sealed
record (capsule-emit-mesh docs/TRUST-MODEL.md §6).

Run: ``python scripts/vendor_tau2_airline_conversations.py
--tau2-results-dir <path to tau2-bench's data/tau2/results/final>``

``--check`` (what CI's vendor-drift workflow runs, see
``.github/workflows/vendor-drift.yml``) re-derives the dataset from
``--tau2-results-dir`` in memory and fails if either the committed
``tau2_conversations_<model>_airline_4trials.jsonl`` or its
``PROVENANCE.json`` entry differs from what that re-derivation produces --
writes nothing. Unlike ``vendor_cpb_registry.py``'s ``--check``, no
provenance field is excluded from the comparison: ``source_git_commit`` is
read out of the results JSON's own ``info.git_commit`` field (data the
source file already carries), not stamped from the checkout's git state, so
it doesn't drift out from under the check the way a real vendor-time commit
sha would.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "capsule_engine" / "examples" / "data" / "tau2_airline"

MODEL_FILES = {
    "claude-3-7-sonnet": "claude-3-7-sonnet-20250219_airline_default_gpt-4.1-2025-04-14_4trials.json",
}


def _agent_usage_for_sim(sim: dict) -> dict | None:
    """Sum ``prompt_tokens`` / ``completion_tokens`` over the AGENT's own
    (``role: assistant``) messages -- the token meter for the model
    ``model_attestation`` attests. ``role: user`` messages carry the
    user-simulator's (a different LLM's) usage and are excluded. Returns
    ``None`` if the source recorded no per-message usage at all (absent stays
    absent -- never a fabricated zero)."""
    prompt = completion = 0
    seen = False
    for m in sim["messages"]:
        if m.get("role") != "assistant":
            continue
        u = m.get("usage")
        if not u:
            continue
        seen = True
        prompt += int(u.get("prompt_tokens", 0) or 0)
        completion += int(u.get("completion_tokens", 0) or 0)
    if not seen:
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _generation_parameters_for_sim(sim: dict, agent_llm_args: dict) -> dict:
    """The generation settings this simulation ran under: run-level
    ``temperature`` (from ``info.agent_info.llm_args``) plus the per-``sim``
    ``seed`` (the actual seed of that run). Honest-by-absence: a key the
    source did not record is simply not present."""
    params: dict = {}
    if "temperature" in agent_llm_args and agent_llm_args["temperature"] is not None:
        params["temperature"] = agent_llm_args["temperature"]
    if sim.get("seed") is not None:
        params["seed"] = sim["seed"]
    return params


def _messages_for_sim(sim: dict) -> list[dict]:
    out = []
    for m in sim["messages"]:
        role = m.get("role")
        if role in ("assistant", "user"):
            content = m.get("content")
            record: dict = {"role": role, "content": content if isinstance(content, str) else ""}
            tool_call_names = [tc["name"] for tc in (m.get("tool_calls") or [])]
            if tool_call_names:
                record["tool_call_names"] = tool_call_names
            out.append(record)
    return out


def _records_for_sims(sims: list[dict], *, model: str, agent_llm_args: dict) -> list[dict]:
    records = []
    for sim in sims:
        messages = _messages_for_sim(sim)
        usage = _agent_usage_for_sim(sim)
        records.append(
            {
                "sim_id": sim["id"],
                "task_id": sim["task_id"],
                "trial": sim["trial"],
                "termination_reason": sim.get("termination_reason"),
                # Recovered inference metadata (see module docstring): the
                # AGENT model, its generation settings, its token meter, and
                # the honest api-served marker (tau2 runs against hosted API
                # models -- no local GPU/VRAM). Cost is deliberately omitted.
                "model": model,
                "served_by": "api",
                "generation_parameters": _generation_parameters_for_sim(sim, agent_llm_args),
                "usage": usage,
                "messages": messages,
            }
        )
    return records


def _jsonl_content(records: list[dict]) -> str:
    return "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)


def _provenance_entry(
    records: list[dict], content: str, *, out_name: str, fname: str, d: dict, agent_llm_args: dict, model: str
) -> dict:
    n_sims = len(records)
    n_messages = sum(len(r["messages"]) for r in records)
    n_with_usage = sum(1 for r in records if r["usage"] is not None)
    return {
        "source_repo": "https://github.com/sierra-research/tau2-bench",
        "source_file": f"data/tau2/results/final/{fname}",
        "source_git_commit": d["info"].get("git_commit"),
        "trials_used": "all (0-3)",
        "num_simulations": n_sims,
        "num_messages": n_messages,
        "output_file": out_name,
        "output_bytes": len(content.encode("utf-8")),
        # Recovered inference metadata provenance: what model, what settings,
        # what meter -- exactly what leaves the capsule with the mesh
        # serving_provenance shape. Cost is deliberately excluded (meter-not-
        # price); tokens are the meter, currency stays out of the sealed record.
        "agent_model": model,
        "agent_temperature": agent_llm_args.get("temperature"),
        "served_by": "api",
        "usage_meter": "prompt_tokens/completion_tokens summed over role:assistant messages "
        "(agent only; user-simulator tokens excluded)",
        "num_simulations_with_usage": n_with_usage,
        "cost_omitted": "per-message currency 'cost' deliberately not vendored (meter-not-price)",
        "note": (
            "conversation-grain companion to tau2_committed_<model>_airline_trial0.jsonl -- "
            "assistant/user message text + tool-call-name trail, for "
            "[ldg-airline-engagement-pack]'s text-reading claims (A1/A3/A5/A7); "
            "now also carries recovered per-sim model/generation_parameters/usage"
        ),
    }


def _check(
    out_path: Path, content: str, provenance_path: Path, model_key: str, expected_entry: dict
) -> int:
    problems = []

    if not out_path.exists():
        raise SystemExit(
            f"{out_path} does not exist -- run scripts/vendor_tau2_airline_conversations.py to create it"
        )
    if out_path.read_text(encoding="utf-8") != content:
        problems.append(f"{out_path} is out of sync with --tau2-results-dir")

    if not provenance_path.exists():
        raise SystemExit(f"{provenance_path} does not exist")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    key = f"conversations-{model_key}"
    if provenance.get(key) != expected_entry:
        problems.append(f"{provenance_path}'s {key!r} entry is out of sync with --tau2-results-dir")

    if problems:
        raise SystemExit(
            "\n".join(problems) + "\n-- run scripts/vendor_tau2_airline_conversations.py and commit the result"
        )
    print(f"{out_path} and {provenance_path}'s {key!r} entry match --tau2-results-dir")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tau2-results-dir", required=True, help="tau2-bench's data/tau2/results/final directory")
    parser.add_argument("--model", default="claude-3-7-sonnet", choices=sorted(MODEL_FILES))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument(
        "--check", action="store_true",
        help="fail if the vendored copy diverges from --tau2-results-dir; write nothing",
    )
    args = parser.parse_args()

    results_dir = Path(args.tau2_results_dir)
    out_dir = Path(args.out_dir)

    fname = MODEL_FILES[args.model]
    src = results_dir / fname
    d = json.loads(src.read_text())
    agent_info = d["info"].get("agent_info") or {}
    model = agent_info.get("llm")
    agent_llm_args = agent_info.get("llm_args") or {}
    sims = sorted(d["simulations"], key=lambda s: (int(s["task_id"]), s["trial"]))
    out_path = out_dir / f"tau2_conversations_{args.model}_airline_4trials.jsonl"

    records = _records_for_sims(sims, model=model, agent_llm_args=agent_llm_args)
    content = _jsonl_content(records)
    entry = _provenance_entry(
        records, content, out_name=out_path.name, fname=fname, d=d, agent_llm_args=agent_llm_args, model=model
    )
    provenance_path = out_dir / "PROVENANCE.json"

    if args.check:
        return _check(out_path, content, provenance_path, args.model, entry)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(
        f"{args.model}: {entry['num_simulations']} simulations, {entry['num_messages']} messages, "
        f"{entry['num_simulations_with_usage']} with usage -> {out_path}"
    )

    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
    provenance[f"conversations-{args.model}"] = entry
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
