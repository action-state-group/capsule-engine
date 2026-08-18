# SPDX-License-Identifier: Apache-2.0
"""Render a ``DryRunReport`` as a self-contained, fragment-carried HTML page.

The emitted HTML/CSS carries **no ledger data** at all -- every row, name,
amount, and cited capsule lives only in the URL fragment (after ``#``),
exactly like this workspace's other fragment-carried verify surfaces. A
browser never sends the fragment portion of a URL in an HTTP request, so
even a "hosted" copy of this page never receives the rows it's showing --
the disclosure line in the share chrome says so because it's literally true
of how the page is built, not just copy. ``verify.js`` (inlined below, no
external ``src``) reads ``location.hash`` on load and re-derives every
number and every cited-capsule digest from it.

One disclosed deviation from the design file: the Google Fonts ``<link>``
tags are dropped (an external network fetch would violate "no network
calls in the page"); the same font-family declarations are kept so a
browser with those fonts installed locally still renders them, with a
close system-font fallback otherwise.

``arm`` (see ``capsule_ledger/packaging.py``) controls whether the evidence
chrome -- the share/permalink row, the redaction disclosure, per-row
capsule fingerprints, the consequential callout's cited-capsule links, the
verify-suggestion in the meta line, and the re-derivation footer -- is
rendered at all in "guards-only". ``verify.js`` itself is never edited for
this: the same script ships unmodified in both arms (so the JS/Python
digest-parity tests keep meaning what they say), and the guards-only CSS
below simply never shows the elements it would have filled in. The
underlying fragment payload is unchanged either way -- "silent in output",
never absent from the machinery.

``telemetry`` (opt-in, see ``capsule_ledger/telemetry/``) is ``None`` by
default, in which case nothing telemetry-related is ever added to the page
and the "no network calls in the page" property holds unconditionally, the
same as before this module knew about telemetry at all. Only when a caller
explicitly passes a ``TelemetryConfig`` (meaning: the operator explicitly
opted in *and* configured a real endpoint when generating this report) does
the page gain one small, disclosed, best-effort beacon -- see
``TelemetryConfig``'s own docstring for exactly what it sends and why.
"""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from importlib import resources

from ..packaging import FULL, GUARDS_ONLY
from .model import DryRunReport, GuardSection

__all__ = [
    "to_fragment_payload",
    "encode_fragment",
    "decode_fragment",
    "render_report_html",
    "TelemetryConfig",
]


@dataclass(frozen=True)
class TelemetryConfig:
    """Explicit, per-report opt-in for the M6 ("viral unit") open-beacon.

    ``endpoint`` has no default anywhere in this codebase -- there is no
    telemetry backend shipped or implied by this package, so a config with
    no endpoint configured is inert by construction, not by a runtime
    check. When both fields are meaningfully set, the rendered page embeds
    a single anonymous ``navigator.sendBeacon`` call on load, deduped per
    browser via localStorage so a refresh never sends twice, carrying only
    ``{"metric": "m6_report_opened", "report_token": <random, generated at
    render time, not derived from any ledger content>}`` -- no agent name,
    amount, or capsule id. Distinguishing "the creator's own first look" from
    "a second party opened it" is deliberately NOT done client-side (this
    page cannot know who it's being opened by): every open of a shared link
    sends one beacon, and a *central* aggregator counts a report_token with
    two or more distinct opens as the second-party event -- consistent with
    this package's own rule that cross-install/cross-party facts are
    computed centrally, never guessed at by a single instance. The
    in-page disclosure banner gains one extra line describing this
    whenever it's active, so a second-party viewer sees the same
    disclosure the creator did.
    """

    opted_in: bool
    endpoint: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.opted_in and self.endpoint)


def _guard_index(report: DryRunReport, guard_id: str) -> int:
    for i, section in enumerate(report.guards):
        if section.guard_id == guard_id:
            return i
    raise KeyError(guard_id)


def _row_payload(row) -> dict:
    return {
        "when": row.when,
        "agent": row.agent,
        "why": row.why,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "capsule": row.capsule,
        "cited_capsule_id": row.cited_capsule_id,
        "cited_capsule": row.cited_capsule,
    }


def _section_payload(section: GuardSection) -> dict:
    return {"id": section.guard_id, "what": section.what, "rows": [_row_payload(r) for r in section.rows]}


def to_fragment_payload(report: DryRunReport) -> dict:
    """The single JSON structure that travels in the URL fragment. Both the
    initial render and every client-side re-derivation read only this."""
    picked = report.consequential()
    consequential = None
    if picked is not None:
        guard_id, row = picked
        section = report.guards[_guard_index(report, guard_id)]
        consequential = {"guard_id": guard_id, "row_index": section.rows.index(row)}

    model_note = None
    if report.model_note is not None:
        model_note = {
            "quote": report.model_note.quote,
            "model_id": report.model_note.model_id,
            "record_count": report.model_note.record_count,
        }

    return {
        "operator": report.operator,
        "agents": list(report.agents),
        "since": report.since_label,
        "actions_replayed": report.actions_replayed,
        "record_range": list(report.record_range),
        "checkpoint": report.checkpoint,
        "replay_command": report.replay_command,
        "generated_at": report.generated_at,
        "guards": [_section_payload(s) for s in report.guards],
        "consequential": consequential,
        "model_note": model_note,
    }


def encode_fragment(payload: dict) -> str:
    """base64url(JSON) -- opaque, URL-safe, no padding. Not a security
    mechanism (the payload is not encrypted); this only makes the payload
    URL-transportable, matching the disclosure line's "travels in the link
    fragment" claim literally."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_fragment(token: str) -> dict:
    token = token.lstrip("#")
    padding = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(token + padding)
    return json.loads(raw.decode("utf-8"))


def _load_verify_js() -> str:
    # "static" is a plain data directory, not a subpackage -- navigated to
    # from the (real, importable) "capsule_ledger.report" package's own
    # traversable root, so no extra __init__.py is needed just for this.
    return resources.files("capsule_ledger.report").joinpath("static", "verify.js").read_text(encoding="utf-8")


_CSS = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #EFE9DF; color: #3D2B1F;
    font-family: 'DM Sans', system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .fp-link, a { color: #C8611A; text-decoration: none; cursor: pointer; }
  .fp-link:hover, a:hover { color: #E8844A; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 44px 40px 90px; }
  .mono { font-family: 'DM Mono', ui-monospace, 'SF Mono', Consolas, monospace; }
  .serif { font-family: 'Playfair Display', ui-serif, Georgia, serif; }
  .share-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; gap: 16px; flex-wrap: wrap; }
  .share-row .mono { font-size: 11px; color: #7A6355; }
  .share-row button { font: inherit; background: none; border: none; color: #C8611A; cursor: pointer; padding: 0; font-size: 11px; font-family: 'DM Mono', monospace; }
  .disclosure { display: flex; align-items: baseline; gap: 8px; margin-bottom: 14px; }
  .disclosure .bang { color: #C8611A; font-weight: 600; font-size: 12px; flex-shrink: 0; }
  .disclosure p { font-size: 12px; font-weight: 300; color: #7A6355; line-height: 1.6; margin: 0; }
  .disclosure strong { font-weight: 500; color: #3D2B1F; }
  .card {
    background: #FDF9F4; border: 1px solid #E8D5B8; border-radius: 20px;
    box-shadow: 0 24px 64px rgba(44,26,14,0.10); overflow: hidden;
  }
  .card-header { padding: 30px 36px 24px; border-bottom: 1px solid #E8D5B8; }
  .card-header-top { display: flex; align-items: center; justify-content: space-between; }
  .wordmark { font-size: 17px; font-weight: 700; letter-spacing: -0.5px; color: #2C1A0E; }
  .badge {
    display: inline-flex; align-items: center; gap: 7px; font-size: 11px; color: #7A6355;
    border: 1px solid #E8D5B8; border-radius: 100px; padding: 4px 12px;
  }
  h1 { font-size: 36px; font-weight: 700; letter-spacing: -1.2px; line-height: 1.12; color: #2C1A0E; margin: 18px 0 8px; }
  h1 em { color: #C8611A; font-style: italic; }
  .subtitle { font-size: 15px; font-weight: 300; line-height: 1.7; color: #7A6355; margin: 0; max-width: 620px; }
  .meta { font-size: 10.5px; color: #7A6355; margin-top: 14px; }
  .headline-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; border-bottom: 1px solid #E8D5B8; }
  .headline-col { padding: 20px 26px; border-right: 1px solid #E8D5B8; }
  .headline-col:last-child { border-right: none; }
  .headline-label-row { display: flex; align-items: baseline; gap: 7px; }
  .headline-check { color: #7A8C6E; font-weight: 600; font-size: 12px; }
  .headline-label { font-size: 12px; font-weight: 300; color: #7A6355; }
  .headline-value { font-size: 30px; font-weight: 500; color: #2C1A0E; margin: 6px 0 3px; }
  .headline-envelope { font-size: 10px; color: #7A8C6E; }
  .consequential {
    margin: 24px 36px 0; border: 1px solid rgba(122,140,110,0.35); background: rgba(122,140,110,0.07);
    border-radius: 14px; padding: 18px 22px; display: flex; gap: 16px; align-items: baseline;
  }
  .consequential-glyph {
    width: 26px; height: 26px; border-radius: 7px; background: #7A8C6E; color: #FDF9F4;
    display: inline-flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600;
    flex-shrink: 0; transform: translateY(5px);
  }
  .consequential-title { font-size: 19px; font-weight: 700; color: #2C1A0E; margin-bottom: 4px; }
  .consequential-body { font-size: 14px; font-weight: 300; line-height: 1.7; color: #3D2B1F; margin: 0; }
  .guard-sections { padding: 26px 36px 8px; }
  .guard-section { margin-bottom: 22px; }
  .guard-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; }
  .guard-id { font-size: 13px; color: #2C1A0E; }
  .guard-what { font-size: 12.5px; font-weight: 300; color: #7A6355; }
  .guard-count { font-size: 11px; color: #7A8C6E; margin-left: auto; }
  .guard-table { border: 1px solid #E8D5B8; border-radius: 12px; overflow: hidden; }
  .guard-row {
    display: grid; grid-template-columns: 96px 168px 1fr 120px; gap: 14px; align-items: baseline;
    padding: 11px 16px; border-bottom: 1px solid #F0E6D6;
  }
  .guard-row:last-child { border-bottom: none; }
  .row-when { font-size: 11px; color: #7A6355; }
  .row-agent { font-size: 11.5px; color: #3D2B1F; }
  .row-why { font-size: 13px; font-weight: 300; color: #3D2B1F; line-height: 1.55; }
  .row-fp { font-size: 10.5px; text-align: right; }
  .tuning-row { display: flex; gap: 16px; margin: 0 36px 26px; flex-wrap: wrap; }
  .model-note { flex: 1.2 1 260px; border: 1px dashed rgba(200,97,26,0.45); border-radius: 14px; padding: 14px 18px; background: rgba(200,97,26,0.04); }
  .model-note-top { display: flex; align-items: baseline; gap: 8px; }
  .model-approx { color: #C8611A; font-weight: 600; font-size: 14px; }
  .model-quote { font-size: 13px; font-style: italic; font-weight: 300; color: #3D2B1F; line-height: 1.65; }
  .model-byline { font-size: 10.5px; color: #C8611A; margin-top: 6px; }
  .tuning-box { flex: 1 1 220px; border: 1px solid #E8D5B8; border-radius: 14px; padding: 14px 18px; background: #FAF7F2; }
  .tuning-label { font-size: 10.5px; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase; color: #7A6355; margin-bottom: 6px; }
  .tuning-cmd { font-size: 11.5px; color: #3D2B1F; line-height: 2; }
  .enforce-band { border-top: 1px solid #E8D5B8; background: #2C1A0E; padding: 22px 36px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  .enforce-title { font-size: 20px; font-weight: 700; color: #FAF7F2; }
  .enforce-body { font-size: 13px; font-weight: 300; color: #D4BC96; margin-top: 3px; }
  .enforce-cmd { font-size: 13px; color: #FAF7F2; background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.15); border-radius: 100px; padding: 11px 22px; white-space: nowrap; }
  .below-card { display: flex; justify-content: space-between; align-items: baseline; margin-top: 16px; gap: 16px; flex-wrap: wrap; }
  .below-note { font-size: 12.5px; font-weight: 300; color: #7A6355; }
  .below-fold { font-size: 10.5px; color: #7A6355; }
  .below-fold.mismatch { color: #B3341F; font-weight: 500; }
  .empty-state { background: #FDF9F4; border: 1px solid #E8D5B8; border-radius: 20px; padding: 60px 36px; text-align: center; color: #7A6355; font-size: 14px; }
  [hidden] { display: none !important; }
"""

# Guards-only ("Arm A"): the evidence chrome above is only ever hidden here,
# never removed from the DOM or from verify.js's own logic -- see this
# module's docstring for why (verify.js stays byte-identical across arms).
_GUARDS_ONLY_CSS = """
  [data-arm="guards-only"] .share-row,
  [data-arm="guards-only"] .disclosure,
  [data-arm="guards-only"] .row-fp,
  [data-arm="guards-only"] .meta-verify-suggestion,
  [data-arm="guards-only"] .below-card,
  [data-arm="guards-only"] [data-consequential-links],
  [data-arm="guards-only"] [data-verified-badge] { display: none !important; }
  [data-arm="guards-only"] .guard-row { grid-template-columns: 96px 168px 1fr; }
"""


def _telemetry_disclosure_line(telemetry: TelemetryConfig | None) -> str:
    if telemetry is None or not telemetry.active:
        return ""
    return (
        ' <span data-telemetry-disclosure>This report was generated with anonymous open-tracking turned on: '
        "opening this link sends one beacon (no row data, just an anonymous per-report token) to "
        f"{telemetry.endpoint!r} to measure sharing.</span>"
    )


def _telemetry_beacon_script(report_token: str, telemetry: TelemetryConfig | None) -> str:
    if telemetry is None or not telemetry.active:
        return ""
    return f"""
<script>
(function () {{
  "use strict";
  var TOKEN = {json.dumps(report_token)};
  var ENDPOINT = {json.dumps(telemetry.endpoint)};
  if (!TOKEN || !ENDPOINT || !navigator.sendBeacon) return;
  var dedupeKey = "capsule_ledger_viral_beacon_" + TOKEN;
  try {{
    if (window.localStorage && localStorage.getItem(dedupeKey)) return;
    if (window.localStorage) localStorage.setItem(dedupeKey, "1");
  }} catch (e) {{ /* localStorage unavailable (private mode, etc.) -- still fine to send once */ }}
  // No row data: an anonymous per-report token only. A single install
  // cannot tell creator from second party -- see TelemetryConfig's
  // docstring on why that's decided centrally, by counting distinct opens.
  navigator.sendBeacon(ENDPOINT, JSON.stringify({{ metric: "m6_report_opened", report_token: TOKEN }}));
}})();
</script>
"""


def _static_html_shell(arm: str = FULL, *, telemetry: TelemetryConfig | None = None) -> str:
    """The generic viewer chrome -- identical for every report of a given
    arm, carries no ledger data. ``verify.js`` fills every dynamic value in
    from the URL fragment at load time; it is never edited per-arm (see
    this module's docstring)."""
    report_token = str(uuid.uuid4())
    extra_css = _GUARDS_ONLY_CSS if arm == GUARDS_ONLY else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dry run report</title>
<style>{_CSS}{extra_css}</style>
</head>
<body data-arm="{arm}">
<div class="wrap">

  <div class="share-row">
    <span class="mono" data-permalink>(open the full shared link to load a report)</span>
    <span class="mono">
      <button type="button" data-copy-permalink>copy permalink</button>
      &nbsp;·&nbsp;
      <button type="button" data-redact>redact for sharing</button>
    </span>
  </div>
  <div class="disclosure" data-disclosure hidden>
    <span class="bang">!</span>
    <p>Sharing this link shares these rows — agent names, counterparties, invoice numbers, amounts. The
      report travels in the link fragment, never on our server. Use <strong>redact for sharing</strong>
      to seal names and amounts first (the seals stay checkable).{_telemetry_disclosure_line(telemetry)}</p>
  </div>

  <div class="empty-state" data-empty-state>
    No report data in this URL. Open the full shared link (the part after <span class="mono">#</span>)
    to load a report — this viewer never fetches data from a server.
  </div>

  <div class="card" data-card hidden>
    <div class="card-header">
      <div class="card-header-top">
        <span class="wordmark serif">capsule <span style="color:#C8611A">guard</span></span>
        <span class="badge mono">dry run — nothing was actually blocked</span>
      </div>
      <h1 class="serif">What the guards <em>would have stopped</em> last week</h1>
      <p class="subtitle">Launch guards replayed over every action your agents took. All of these actions
        executed for real — this is what enforcement would have held for review.</p>
      <div class="meta mono">operator <span data-operator></span> · <span data-agent-count></span> agents ·
        derived from records <span data-record-range></span> under checkpoint #<span data-checkpoint></span>
        <span class="meta-verify-suggestion"> · replayable by anyone: <span data-verify-command></span></span></div>
    </div>

    <div class="headline-grid">
      <div class="headline-col">
        <div class="headline-label-row"><span class="headline-check">✓</span>
          <span class="headline-label">actions replayed</span></div>
        <div class="headline-value mono" data-headline-replayed></div>
        <div class="headline-envelope mono" data-headline-replayed-envelope></div>
      </div>
      <div class="headline-col">
        <div class="headline-label-row"><span class="headline-check">✓</span>
          <span class="headline-label">would have been held</span></div>
        <div class="headline-value mono" data-headline-held></div>
        <div class="headline-envelope mono" data-headline-held-envelope></div>
      </div>
      <div class="headline-col">
        <div class="headline-label-row"><span class="headline-check">✓</span>
          <span class="headline-label">value held for review</span></div>
        <div class="headline-value mono" data-headline-value></div>
        <div class="headline-envelope mono">each currency counted separately, never summed</div>
      </div>
    </div>

    <div class="consequential" data-consequential hidden>
      <span class="consequential-glyph">⊘</span>
      <div>
        <div class="consequential-title serif">The one that pays for the week</div>
        <p class="consequential-body" data-consequential-body></p>
        <div class="row-fp mono" data-consequential-links></div>
      </div>
    </div>

    <div class="guard-sections" data-guard-sections></div>

    <div class="tuning-row" data-model-note-row hidden>
      <div class="model-note">
        <div class="model-note-top"><span class="model-approx">≈</span>
          <span class="model-quote" data-model-quote></span></div>
        <div class="model-byline mono" data-model-byline></div>
      </div>
      <div class="tuning-box">
        <div class="tuning-label">Tune before you enforce</div>
        <div class="tuning-cmd mono">
          capsule guard set &lt;action-class&gt;-cap &lt;new-value&gt;  <span style="color:#C8611A"># value from the note above — your call</span><br>
          <span data-tuning-recheck></span>   <span style="color:#7A8C6E"># re-check</span>
        </div>
      </div>
    </div>

    <div class="enforce-band">
      <div style="flex:1">
        <div class="enforce-title serif">Ready to make it real?</div>
        <div class="enforce-body">Enforcement is one line. From then on, these <span data-enforce-count></span>
          would have been held for review — each refusal a signed record, proof the guard ran.</div>
      </div>
      <span class="enforce-cmd mono">$ capsule guard enforce</span>
    </div>
  </div>

  <div class="below-card" data-below-card hidden>
    <span class="below-note">This page re-derives its numbers from the cited records when opened — if the
      ledger changed, the report says so.</span>
    <span class="below-fold mono" data-verified-badge></span>
  </div>
</div>

<template id="section-template">
  <div class="guard-section">
    <div class="guard-head">
      <span class="guard-id mono" data-guard-id></span>
      <span class="guard-what" data-guard-what></span>
      <span class="guard-count mono" data-guard-count></span>
    </div>
    <div class="guard-table" data-guard-table></div>
  </div>
</template>

<template id="row-template">
  <div class="guard-row">
    <span class="row-when mono" data-row-when></span>
    <span class="row-agent mono" data-row-agent></span>
    <span class="row-why" data-row-why></span>
    <span class="row-fp mono fp-link" data-row-fp></span>
  </div>
</template>

<script>
{_load_verify_js()}
</script>
{_telemetry_beacon_script(report_token, telemetry)}</body>
</html>
"""


def render_report_html(
    report: DryRunReport, *, arm: str = FULL, telemetry: TelemetryConfig | None = None
) -> tuple[str, str]:
    """Returns ``(html, fragment)``. ``html`` carries no ledger data at all;
    open it as ``<file-or-url>#<fragment>`` to see the report -- see this
    module's docstring for why that split is the point, not an inconvenience.
    ``arm`` and ``telemetry`` only affect the static shell (evidence chrome
    visibility, the optional open-beacon); the fragment payload is always
    the full, real report data either way."""
    html = _static_html_shell(arm, telemetry=telemetry)
    fragment = encode_fragment(to_fragment_payload(report))
    return html, fragment
