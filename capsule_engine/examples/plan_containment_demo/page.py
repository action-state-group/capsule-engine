# SPDX-License-Identifier: Apache-2.0
"""C5 (``[ldg-plan-containment]``): the two-lane demo page -- a single,
self-contained, offline-openable HTML artifact rendering all three runs
(design doc §4: "A single interleaved timeline, two lanes (conversation ·
tool calls), one ledger, one chain").

Same "disclose it, then recompute it in the browser" trick this codebase's
other hand-authored evidence pages use (this module's own docstring cites
the PM companion page this pattern is drawn from): the refusal card's
evidence object is embedded verbatim and re-hashed client-side (pure-JS
SHA-256 over the RFC 8785 JCS bytes -- the exact algorithm
``agent_action_capsule.canonical.json_digest`` uses -- see
``_JCS_AND_SHA256_JS`` below) against the digest the sealed capsule actually
committed to (``constraints[].evidence_digest``). A stranger does not have
to trust this page's own rendering of the evidence; they can watch it
re-derive the same digest, in their own browser, with no network call.

**Sentence vocabulary.** Rows are rendered from one small, event-keyed
dispatch table (``_row_for_record``) -- the same shape
``[ldg-viewer-plain-english-column]`` is scoped to build for the general
bundle viewer (one vocabulary, two consumers, per that task's own text).
This module's table is deliberately narrow (only the record kinds this
demo's own chain produces); it is not vendored from or by that task, so the
two can be built and reviewed independently.

**Reads vs. writes.** Only tool-call WRITES were ever mechanically gated
(``guards/checks/plan_containment.py``, run through ``GuardEngine.check()``);
a READ's "in plan"/"step N" annotation here is computed for display only
(``plan.step_index(verb)``), never a claim that the read was itself gated --
see ``demo.py``'s own module docstring for why.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from capsule_engine.guards.plan import PlanDefinition

from .demo import DemoResult

__all__ = ["render_demo_page"]

_ICONS = {"conversation": "\U0001f4ac", "tool_call": "\U0001f527", "judge": "⚖", "system": "\U0001f4ca"}

_RUN_TITLES = {
    "run-a": ("Run A -- the good path", "Containment passes throughout; the outcome is attained."),
    "run-b": ("Run B -- the departure", "An injected instruction is refused before it can execute."),
    "run-c": ("Run C -- the honest one", "Every containment check passes; the outcome is not attained."),
}


def _payload(capsule: dict) -> dict:
    return capsule.get("asg_payload") or {}


def _row_for_record(capsule: dict, plan: PlanDefinition) -> dict:
    """One event -> one sentence, never a verdict beyond what the record
    itself states (mirrors ``[ldg-viewer-plain-english-column]``'s own
    "sentences state what the record IS" rule)."""
    payload = _payload(capsule)
    event = payload.get("event")
    detail = payload.get("detail") or {}
    action_type = capsule.get("action_type")

    if event == "conversation_turn":
        return {
            "lane": "conversation",
            "sentence": f"{detail.get('speaker_role')} spoke -- turn {detail.get('turn_index', 0) + 1}",
            "meta": {},
        }
    if event == "conversation_session_close":
        return {
            "lane": "system",
            "sentence": f"session closed -- {detail.get('turn_count')} turns",
            "meta": {},
        }
    if event == "judge_judgment":
        confidence = detail.get("confidence_micros", 0) / 1_000_000
        return {
            "lane": "judge",
            "sentence": f"judge recorded: {detail.get('label')} (confidence {confidence:.2f})",
            "meta": {"prompt_digest": detail.get("prompt_digest", "")[:16] + "…"},
        }
    if capsule.get("effect") and payload.get("connector_type"):
        effect = capsule["effect"]
        return {
            "lane": "system",
            "sentence": f"{payload.get('predicate')} confirmed via {payload.get('connector_type')}",
            "meta": {"status": effect.get("status"), "attestation": effect.get("effect_attestation")},
        }
    if action_type == "decide":
        verb = (capsule.get("action_id") or "").split("/", 1)[0]
        step = plan.step_index(verb)
        constraints = {c["id"]: c for c in capsule.get("constraints", [])}
        plan_result = (constraints.get("plan_containment") or {}).get("result")
        in_plan = step is not None
        label = f"step {step + 1} of plan" if in_plan else "not in plan"
        decision = capsule.get("disposition", {}).get("decision")
        return {
            "lane": "tool_call",
            "sentence": f"{verb} -- {label}",
            "meta": {
                "containment": plan_result,
                "decision": decision,
                "kind": "write",
            },
        }
    # A tool-call READ: any other fyi capsule whose detail carries the
    # tool_call lane marker (guards/tool_call.py's TOOL_CALL_LANE).
    if detail.get("lane") == "tool_call":
        verb = event
        step = plan.step_index(verb)
        label = f"step {step + 1} of plan" if step is not None else "not a plan step"
        return {
            "lane": "tool_call",
            "sentence": f"{verb} -- recorded (read, {label})",
            "meta": {"kind": "read", "content_digest": (detail.get("content_digest") or "")[:16] + "…"},
        }
    return {"lane": "system", "sentence": event or action_type or "record", "meta": {}}


def _rows_for_run(result: DemoResult, plan: PlanDefinition) -> list[dict]:
    rows = []
    for record in result.records:
        row = _row_for_record(record.capsule, plan)
        row["capsule_id"] = record.capsule_id
        row["timestamp"] = record.capsule.get("timestamp")
        rows.append(row)
    return rows


def _refusal_card(result: DemoResult) -> dict | None:
    """Run B's evidence, disclosed for in-browser recompute -- ``None`` for
    a run with no departure (nothing to recompute)."""
    for key, evidence in result.constraint_evidence.items():
        capsule_id = result.capsule_ids.get(key)
        record = next((r for r in result.records if r.capsule_id == capsule_id), None)
        if record is None:
            continue
        constraint = next((c for c in record.capsule["constraints"] if c["id"] == "plan_containment"), None)
        if constraint is None or constraint["result"] != "fail":
            continue
        return {"verb": key.removeprefix("write_"), "evidence": evidence, "evidence_digest": constraint["evidence_digest"]}
    return None


_JCS_AND_SHA256_JS = r"""
function jcsValue(v){
  if(v===null||v===undefined) return "null";
  if(v===true) return "true";
  if(v===false) return "false";
  if(typeof v==="number") return String(v);
  if(typeof v==="string"){
    var out=['"'];
    for(var i=0;i<v.length;i++){
      var ch=v[i], o=v.charCodeAt(i);
      if(ch==='"') out.push('\\"');
      else if(ch==='\\') out.push('\\\\');
      else if(o<0x20) out.push('\\u'+('0000'+o.toString(16)).slice(-4));
      else out.push(ch);
    }
    out.push('"');
    return out.join('');
  }
  if(Array.isArray(v)) return "["+v.map(jcsValue).join(",")+"]";
  if(typeof v==="object"){
    var keys=Object.keys(v).sort();
    return "{"+keys.map(function(k){return jcsValue(k)+":"+jcsValue(v[k]);}).join(",")+"}";
  }
  throw new Error("not JSON-serializable: "+v);
}
function sha256(bytes){
  function rr(n,x){return (x>>>n)|(x<<(32-n));}
  var K=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  var H=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  var b=bytes, l=b.length*8;
  var wl=(((b.length+8)>>6)+1)*16, m=new Uint32Array(wl);
  for(var i=0;i<b.length;i++) m[i>>2]|=b[i]<<(24-(i%4)*8);
  m[b.length>>2]|=0x80<<(24-(b.length%4)*8); m[wl-1]=l>>>0; m[wl-2]=Math.floor(l/4294967296);
  var w=new Uint32Array(64);
  for(var j=0;j<wl;j+=16){
    for(var t=0;t<16;t++) w[t]=m[j+t];
    for(t=16;t<64;t++){
      var s0=rr(7,w[t-15])^rr(18,w[t-15])^(w[t-15]>>>3);
      var s1=rr(17,w[t-2])^rr(19,w[t-2])^(w[t-2]>>>10);
      w[t]=(w[t-16]+s0+w[t-7]+s1)>>>0;}
    var a=H[0],bb=H[1],c=H[2],d=H[3],e=H[4],f=H[5],g=H[6],h=H[7];
    for(t=0;t<64;t++){
      var S1=rr(6,e)^rr(11,e)^rr(25,e), ch=(e&f)^(~e&g);
      var t1=(h+S1+ch+K[t]+w[t])>>>0;
      var S0=rr(2,a)^rr(13,a)^rr(22,a), mj=(a&bb)^(a&c)^(bb&c);
      var t2=(S0+mj)>>>0;
      h=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=bb;bb=a;a=(t1+t2)>>>0;}
    H[0]=(H[0]+a)>>>0;H[1]=(H[1]+bb)>>>0;H[2]=(H[2]+c)>>>0;H[3]=(H[3]+d)>>>0;
    H[4]=(H[4]+e)>>>0;H[5]=(H[5]+f)>>>0;H[6]=(H[6]+g)>>>0;H[7]=(H[7]+h)>>>0;}
  return Array.from(H).map(function(x){return ("00000000"+x.toString(16)).slice(-8);}).join("");
}
function normalize(v){
  // Mirrors agent_action_capsule.canonical.normalize exactly: remove
  // members whose value is null, an empty array, or an empty object,
  // bottom-up -- json_digest is SHA-256(JCS(normalize(v))), never
  // SHA-256(JCS(v)) directly, so recompute must normalize too or a
  // null-valued field (e.g. plan_containment's own step_index on a
  // departure) makes an honest recompute look like a mismatch.
  if(Array.isArray(v)) return v.map(normalize);
  if(v && typeof v==="object"){
    var out = {};
    for(var k in v){
      if(!Object.prototype.hasOwnProperty.call(v,k)) continue;
      var nv = normalize(v[k]);
      if(nv===null||nv===undefined) continue;
      if(Array.isArray(nv) && nv.length===0) continue;
      if(!Array.isArray(nv) && typeof nv==="object" && Object.keys(nv).length===0) continue;
      out[k]=nv;
    }
    return out;
  }
  return v;
}
function jsonDigest(value){
  var s = jcsValue(normalize(value));
  return sha256(new TextEncoder().encode(s));
}
"""


def _lane_label(lane: str) -> str:
    return {"conversation": "conversation", "tool_call": "tool call", "judge": "judge", "system": "system"}.get(lane, lane)


def _render_run_section(run_key: str, result: DemoResult, plan: PlanDefinition) -> str:
    title, subtitle = _RUN_TITLES[run_key]
    rows = _rows_for_run(result, plan)
    row_html = []
    for row in rows:
        icon = _ICONS.get(row["lane"], "•")
        meta_bits = " · ".join(f"{k}={v}" for k, v in row["meta"].items() if v is not None)
        badge = ""
        if row["meta"].get("containment"):
            cls = "pass" if row["meta"]["containment"] == "pass" else "fail"
            badge = f'<span class="badge {cls}">{html.escape(str(row["meta"]["containment"]))}</span>'
        row_html.append(
            f'<div class="row lane-{row["lane"]}">'
            f'<span class="icon">{icon}</span>'
            f'<span class="lane-tag">{_lane_label(row["lane"])}</span>'
            f'<span class="sentence">{html.escape(row["sentence"])}</span>'
            f"{badge}"
            f'<span class="meta">{html.escape(meta_bits)}</span>'
            f'<span class="cid">{html.escape(row["capsule_id"][:12])}…</span>'
            "</div>"
        )

    attained = result.fold.get("attained")
    fold_html = (
        f'<div class="fold {"attained" if attained else "not-attained"}">'
        f'attainment: <strong>{"attained" if attained else "not attained"}</strong>'
        f' -- {html.escape(result.fold.get("coverage_judged", ""))}, '
        f'{html.escape(result.fold.get("coverage_agreement", ""))}, '
        f'{html.escape(result.fold.get("coverage_confirmed", ""))}'
        "</div>"
    )

    refusal_html = ""
    card = _refusal_card(result)
    if card is not None:
        evidence_json = json.dumps(card["evidence"], indent=2)
        refusal_html = f"""
        <div class="refusal">
          <h3>Refusal evidence -- recomputed in your browser, right now</h3>
          <pre class="evidence" id="evidence-{run_key}">{html.escape(evidence_json)}</pre>
          <div class="recheck" id="recheck-{run_key}" data-digest="{html.escape(card['evidence_digest'])}">checking…</div>
        </div>
        """

    return f"""
    <section id="{run_key}" class="run">
      <h2>{html.escape(title)}</h2>
      <p class="subtitle">{html.escape(subtitle)}</p>
      <div class="timeline">{''.join(row_html)}</div>
      {fold_html}
      {refusal_html}
    </section>
    """


def render_demo_page(plan: PlanDefinition, manifest_digest: str, results: dict[str, DemoResult]) -> str:
    """Render the whole two-lane demo page. ``results`` keys are
    ``"run-a"``/``"run-b"``/``"run-c"`` -> ``DemoResult``."""
    plan_actions_html = "".join(
        f"<li><code>{html.escape(a)}</code></li>" for a in plan.allowed_actions
    )
    preconditions_html = "".join(
        f"<li><code>{html.escape(p.action)}</code> requires citing: {html.escape(p.citing)}</li>"
        for p in plan.preconditions
    )
    binding_html = ", ".join(f"{k}={v}" for k, v in plan.binding.items())

    sections = "".join(_render_run_section(key, results[key], plan) for key in ("run-a", "run-b", "run-c") if key in results)

    recompute_calls = "\n".join(
        f"""
        (function(){{
          var el = document.getElementById("recheck-{key}");
          if(!el) return;
          var evidence = {json.dumps(_refusal_card(results[key])['evidence'])};
          var got = jsonDigest(evidence);
          var want = el.getAttribute("data-digest");
          if(got === want){{
            el.className = "recheck ok";
            el.textContent = "✓ this evidence hashes to the digest the refusal capsule committed to -- recomputed here, in your browser, offline";
          }} else {{
            el.className = "recheck bad";
            el.textContent = "✗ MISMATCH -- recomputed " + got + " but the capsule committed to " + want;
          }}
        }})();
        """
        for key in results
        if _refusal_card(results[key]) is not None
    )

    plan_digest_check_js = f"""
    (function(){{
      var plan = {json.dumps(plan.canonical_dict())};
      var got = jsonDigest(plan);
      var want = {json.dumps(plan.definition_digest())};
      var el = document.getElementById("plan-digest-check");
      if(got === want){{
        el.className = "recheck ok";
        el.textContent = "✓ this plan hashes to the digest pinned above -- recomputed here, in your browser, offline";
      }} else {{
        el.className = "recheck bad";
        el.textContent = "✗ MISMATCH";
      }}
    }})();
    """

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plan containment -- declared, compiled, checked</title>
<style>
 :root {{ --ink:#111418; --dim:#6b7280; --line:#e5e7eb; --bg:#fff; --pass:#15803d; --fail:#b91c1c; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:#f7f8fa; color:var(--ink);
         font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
 .wrap {{ max-width:960px; margin:0 auto; padding:40px 24px 80px; }}
 h1 {{ font-size:26px; margin:0 0 8px; letter-spacing:-.02em; }}
 h2 {{ font-size:19px; margin:28px 0 2px; }}
 h3 {{ font-size:15px; margin:18px 0 8px; }}
 .sub {{ color:var(--dim); font-size:14px; }}
 nav {{ display:flex; gap:10px; margin:18px 0 24px; }}
 nav a {{ text-decoration:none; color:var(--ink); background:#fff; border:1px solid var(--line);
          border-radius:6px; padding:6px 12px; font-size:13px; }}
 .plan-box {{ background:#fff; border:1px solid var(--line); border-left:3px solid var(--ink);
              border-radius:6px; padding:16px 18px; margin:16px 0; }}
 .plan-box code {{ font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; background:#f6f7f9;
                    padding:1px 5px; border-radius:3px; }}
 .digest {{ font-size:12px; color:var(--dim); word-break:break-all; margin-top:8px; }}
 .run {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:18px 20px; margin:22px 0; }}
 .subtitle {{ color:var(--dim); font-size:13.5px; margin:2px 0 14px; }}
 .timeline {{ display:flex; flex-direction:column; gap:6px; }}
 .row {{ display:flex; align-items:center; gap:10px; padding:7px 10px; border-radius:5px; font-size:13.5px; }}
 .row.lane-conversation {{ background:#eff6ff; }}
 .row.lane-tool_call {{ background:#fdf4ff; }}
 .row.lane-judge {{ background:#fffbeb; }}
 .row.lane-system {{ background:#f0fdf4; }}
 .icon {{ flex:0 0 auto; }}
 .lane-tag {{ flex:0 0 90px; font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); }}
 .sentence {{ flex:1; }}
 .badge {{ font-size:10.5px; font-weight:700; padding:2px 7px; border-radius:4px; }}
 .badge.pass {{ color:var(--pass); background:#ecfdf5; }}
 .badge.fail {{ color:var(--fail); background:#fef2f2; }}
 .meta {{ color:var(--dim); font-size:11.5px; }}
 .cid {{ color:var(--dim); font-size:11px; font-family:ui-monospace,monospace; }}
 .fold {{ margin-top:14px; padding:10px 14px; border-radius:6px; font-size:13.5px; }}
 .fold.attained {{ background:#ecfdf5; color:var(--pass); }}
 .fold.not-attained {{ background:#fef2f2; color:#92400e; }}
 .refusal {{ margin-top:16px; padding-top:14px; border-top:1px dashed var(--line); }}
 .evidence {{ background:#0b1020; color:#e5e7eb; padding:12px 14px; border-radius:6px; font-size:12.5px;
              overflow-x:auto; }}
 .recheck {{ font-size:12.5px; margin-top:8px; color:var(--dim); }}
 .recheck.ok {{ color:var(--pass); }}
 .recheck.bad {{ color:var(--fail); font-weight:600; }}
 .contrast {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:24px 0; }}
 .contrast .col {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px 18px; font-size:13.5px; }}
 footer {{ margin-top:30px; font-size:12.5px; color:var(--dim); }}
</style></head><body><div class="wrap">

<header>
  <h1>One declaration. Compiled forward, and compiled backward.</h1>
  <p class="sub">The same declared outcome checked at act time (containment) and at report time (attainment) --
  two different questions, checked separately, on the same chain.</p>
</header>

<nav><a href="#run-a">Run A -- the good path</a><a href="#run-b">Run B -- the departure</a><a href="#run-c">Run C -- the honest one</a></nav>

<div class="plan-box">
  <strong>Declared outcome:</strong> <code>{html.escape(plan.outcome_id)}</code><br>
  <strong>Allowed actions</strong> (in order): <ul>{plan_actions_html}</ul>
  <strong>Bound to:</strong> {html.escape(binding_html)}<br>
  {"<strong>Preconditions:</strong><ul>" + preconditions_html + "</ul>" if preconditions_html else ""}
  <div class="digest">plan digest: {html.escape(plan.definition_digest())}</div>
  <div class="digest">manifest digest: {html.escape(manifest_digest)}</div>
  <div class="recheck" id="plan-digest-check">checking…</div>
</div>

{sections}

<div class="contrast">
  <div class="col">
    <h3>Inferred intent</h3>
    <p>This action occurred, was allowed, and looked related to the original request under a
    configured sensitivity setting.</p>
  </div>
  <div class="col">
    <h3>Declared intent</h3>
    <p>This action occurred in service of declared outcome <code>{html.escape(plan.outcome_id)}</code>,
    at a specific step of a compiled plan, digest <code>{html.escape(plan.definition_digest()[:16])}…</code> --
    and here is the plan.</p>
  </div>
</div>

<footer>
  <p>Containment answers "was this action inside the plan that was supposed to serve the outcome" --
  it never answers "was the outcome reached." Run C is why that distinction matters: every containment
  check above passes, and the outcome is still not attained -- containment and attainment stay two
  separate questions, always. A judge's recorded label is a claim, never an enforcement action --
  containment blocks, the judge only ever records.</p>
</footer>

</div>
<script>
{_JCS_AND_SHA256_JS}
{plan_digest_check_js}
{recompute_calls}
</script>
</body></html>"""


def write_demo_page(out_path: str | Path) -> Path:
    """Build all three runs fresh (fresh temp ledgers, default seed) and
    render the page to ``out_path``. Convenience entry point for manual
    review / the browser-verification step -- not used by the test suite,
    which renders against already-built ``DemoResult``s instead."""
    import shutil
    import tempfile

    from .demo import DEFAULT_SEED, load_plan, run_a, run_b, run_c

    plan, manifest_digest = load_plan()
    results = {}
    for key, fn in (("run-a", run_a), ("run-b", run_b), ("run-c", run_c)):
        tmp_dir = tempfile.mkdtemp(prefix="capsule-ledger-plan-containment-page-")
        try:
            results[key] = fn(tmp_dir, seed=DEFAULT_SEED)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    html_text = render_demo_page(plan, manifest_digest, results)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "plan_containment_demo_page.html"
    written = write_demo_page(target)
    print(f"wrote {written}")
