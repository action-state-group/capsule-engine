// SPDX-License-Identifier: Apache-2.0
//
// conversation_exchange_card.js -- the tau2 / conversation_exchange DOMAIN
// MODULE. It plugs into the base viewer (capsule_viewer.js) on the registry
// seam and supplies ONLY the card body for `conversation_exchange` capsules.
// It owns NO canonicalization, NO fragment carry, and NO capsule_id recompute:
// the base already ran the recompute and handed it to us as `entry.recompute`,
// and stamps the shared ✓/✗ chip + security toggle around whatever node we
// return. If this file ever needed a digest, that would be the signal it is
// doing the base's job -- route it through the base instead.
//
// What the card renders, words-first (mirroring capsule-emit-mesh's mesh
// viewer's readability):
//   * LEAD with the conversation -- the assistant/user turns + the tool-call
//     NAME trail, from the operator-disclosed `entry.conversation.messages`
//     block carried in the fragment (the sealed capsule holds only DIGESTS, so
//     the readable transcript is a deliberate disclosure, not the capsule body).
//   * "What ran" -- a friendly model name (never a raw id/hash in the default
//     view), a "generated with: temperature 0, seed …" line, the in/out token
//     split ("29187 in / 738 out / 29925 total"), and served_by:"api" shown
//     honestly as "API-served — no local hardware" (never an invented GPU).
//   * Honesty about absent fields: usage/gen-params that the record does not
//     carry are said to be absent, never faked.
(function () {
  "use strict";

  if (typeof window === "undefined" || !window.CapsuleViewer) {
    // The base must load first (the shell inlines it before this module).
    throw new Error("conversation_exchange_card.js: base CapsuleViewer not loaded");
  }

  // A friendly, human model name for the DEFAULT view -- never a raw hash. tau2
  // seals a real API model id like "claude-3-7-sonnet-20250219"; title-case the
  // family tail and drop the trailing date-version so the card reads as words.
  function friendlyModelName(modelId) {
    if (!modelId) return "(model not named in record)";
    var base = String(modelId);
    // strip a trailing yyyymmdd date-version ("-20250219")
    base = base.replace(/-\d{8}$/, "");
    // Title-case dash-separated tokens: "claude-3-7-sonnet" -> "Claude-3.7-Sonnet"
    var parts = base.split("-");
    var titled = parts.map(function (p, i) {
      if (/^\d+$/.test(p)) {
        // a bare numeric segment: join to the previous number with a dot so
        // "3","7" reads "3.7" -- but only when both neighbours are numeric.
        return p;
      }
      return p.charAt(0).toUpperCase() + p.slice(1);
    });
    // collapse consecutive numeric tokens with a dot (3-7 -> 3.7)
    var out = [];
    for (var i = 0; i < titled.length; i++) {
      if (i > 0 && /^\d+$/.test(titled[i]) && /^[\d.]+$/.test(out[out.length - 1])) {
        out[out.length - 1] = out[out.length - 1] + "." + titled[i];
      } else {
        out.push(titled[i]);
      }
    }
    return out.join("-");
  }

  // "generated with: temperature 0, seed 626729" from the sealed
  // generation_parameters. temperature is stringified in the capsule ("0.0");
  // render it tidily. Absent params are simply omitted (honest by absence).
  function genParamsLine(gp) {
    if (!gp) return null;
    var bits = [];
    if (gp.temperature !== undefined && gp.temperature !== null) {
      var t = String(gp.temperature);
      // "0.0" -> "0" for readability; keep any real fractional value as-is.
      if (/^\d+\.0+$/.test(t)) t = String(parseInt(t, 10));
      bits.push("temperature " + t);
    }
    if (gp.seed !== undefined && gp.seed !== null) bits.push("seed " + gp.seed);
    if (!bits.length) return null;
    return "generated with: " + bits.join(", ");
  }

  // "29187 in / 738 out / 29925 total" -- the token METER split, or an honest
  // absence note when the record carries no usage block.
  function usageLine(usage) {
    if (!usage) return { text: "token usage: not recorded in this capsule", absent: true };
    var pt = usage.prompt_tokens,
      ct = usage.completion_tokens,
      tt = usage.total_tokens;
    if (pt == null && ct == null && tt == null) {
      return { text: "token usage: not recorded in this capsule", absent: true };
    }
    var parts = [];
    if (pt != null) parts.push(pt + " in");
    if (ct != null) parts.push(ct + " out");
    if (tt != null) parts.push(tt + " total");
    return { text: parts.join(" / "), absent: false };
  }

  // served_by:"api" is shown honestly -- API-served means there is no local GPU
  // to attest, so we say exactly that rather than invent hardware.
  function servedLine(servedBy, hardware) {
    if (hardware) return { text: "hardware: " + hardware, api: false };
    if (servedBy === "api") return { text: "API-served — no local hardware", api: true };
    if (servedBy) return { text: "served by: " + servedBy, api: false };
    return null;
  }

  // The card body. `entry` carries: capsule_id, record (the sealed capsule),
  // recompute (base-computed), and conversation.messages (operator-disclosed
  // readable turns). `h` is the base's shared DOM helpers.
  function renderConversationExchangeCard(entry, h) {
    var rec = entry.record || {};
    var ma = rec.model_attestation || {};
    var ca = ma.compute_attestation || {};
    var conv = entry.conversation || {};
    var card = h.el("div", "conv-card");

    // ---- LEAD: the conversation / task, words-first --------------------
    var convWrap = h.el("div", "conv");
    var convHead = h.el("div", "conv-head", "Conversation");
    if (conv.disclosed) {
      convHead.appendChild(h.el("span", "conv-tag shown", "shown by operator"));
    } else {
      convHead.appendChild(h.el("span", "conv-tag sealed", "sealed — digest only"));
    }
    convWrap.appendChild(convHead);

    var messages = conv.messages || [];
    if (messages.length) {
      messages.forEach(function (m) {
        var turn = h.el("div", "turn " + (m.role || "other"));
        var who = m.role === "assistant" ? "Assistant" : m.role === "user" ? "User" : m.role || "—";
        turn.appendChild(h.el("div", "turn-role", who));
        if (m.content) turn.appendChild(h.el("div", "turn-text", m.content));
        // the tool-call NAME trail -- names only (tau2 flattens to name).
        if (m.tool_call_names && m.tool_call_names.length) {
          var trail = h.el("div", "tool-trail");
          trail.appendChild(h.el("span", "tool-label", "tool calls:"));
          m.tool_call_names.forEach(function (name) {
            trail.appendChild(h.el("span", "tool-chip", name));
          });
          turn.appendChild(trail);
        }
        convWrap.appendChild(turn);
      });
    } else {
      convWrap.appendChild(
        h.el(
          "div",
          "turn-text muted",
          "The transcript was not disclosed with this bundle — the capsule seals it as digests only."
        )
      );
    }
    card.appendChild(convWrap);

    // ---- WHAT RAN ------------------------------------------------------
    var ran = h.el("div", "what-ran");
    ran.appendChild(h.el("div", "what-ran-title", "What ran"));

    var model = h.el("div", "ran-model");
    model.appendChild(h.el("span", "ran-model-name", friendlyModelName(ma.model_id)));
    if (ma.provider) model.appendChild(h.el("span", "ran-provider", "via " + ma.provider));
    ran.appendChild(model);

    var gpl = genParamsLine(ca.generation_parameters);
    if (gpl) ran.appendChild(h.el("div", "ran-line", gpl));

    var ul = usageLine(ca.usage);
    ran.appendChild(h.el("div", "ran-line" + (ul.absent ? " muted" : ""), ul.text));

    var sl = servedLine(ca.served_by, ca.hardware);
    if (sl) ran.appendChild(h.el("div", "ran-line" + (sl.api ? " api" : ""), sl.text));

    card.appendChild(ran);

    // ---- ACCOUNTABILITY (plain default) -------------------------------
    // The base already stamped the recompute chip in the entry head and the
    // sealed digests behind the toggle; here we give the plain-language line.
    var acct = h.el("div", "accountability");
    if (entry.recompute && entry.recompute.matches === true) {
      acct.appendChild(
        h.el(
          "div",
          "acct-ok",
          "✓ This record's capsule_id was recomputed in your browser and matches — nothing was changed."
        )
      );
    } else if (entry.recompute && entry.recompute.matches === false) {
      acct.appendChild(
        h.el("div", "acct-fail", "✗ The recomputed capsule_id does NOT match — this record was altered.")
      );
    } else {
      acct.appendChild(h.el("div", "acct-muted", "— capsule_id could not be recomputed here."));
    }
    card.appendChild(acct);

    return card;
  }

  window.CapsuleViewer.register("conversation_exchange", renderConversationExchangeCard);

  // Expose the pure helpers so the JS-parity test can drive them headlessly.
  window.__conversationExchangeCard = {
    friendlyModelName: friendlyModelName,
    genParamsLine: genParamsLine,
    usageLine: usageLine,
    servedLine: servedLine,
  };
})();
