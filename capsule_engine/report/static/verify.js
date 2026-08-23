// SPDX-License-Identifier: Apache-2.0
//
// Dry-run report viewer: reads the report payload from the URL fragment
// (never a fetch, never a server round trip -- the fragment is never sent
// over the wire by the browser) and re-derives every number on this page
// from it. Also re-verifies each cited capsule's own capsule_id by
// recomputing it, so a tampered fragment or an altered cited record is
// caught here, not just trusted.
//
// The canonicalization below (normalize/jcsString/jcsValue) is a hand port
// of agent_action_capsule/canonical.py's JCS subset (RFC 8785): absent-field
// normalization, then lexicographic-by-UTF-16-code-unit key ordering, then
// SHA-256 over the UTF-8 bytes. It must stay byte-for-byte in step with
// that module -- this is the client-side half of the same digest.
(function () {
  "use strict";

  // ---- canonicalization (mirrors agent_action_capsule/canonical.py) ----

  function normalize(v) {
    if (Array.isArray(v)) {
      return v.map(normalize);
    }
    if (v !== null && typeof v === "object") {
      var out = {};
      Object.keys(v).forEach(function (k) {
        var nv = normalize(v[k]);
        if (nv === null || nv === undefined) return;
        if (Array.isArray(nv) && nv.length === 0) return;
        if (!Array.isArray(nv) && typeof nv === "object" && Object.keys(nv).length === 0) return;
        out[k] = nv;
      });
      return out;
    }
    return v === undefined ? null : v;
  }

  function jcsString(s) {
    var out = ['"'];
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      var code = s.charCodeAt(i);
      if (ch === '"') out.push('\\"');
      else if (ch === "\\") out.push("\\\\");
      else if (code === 0x08) out.push("\\b");
      else if (code === 0x09) out.push("\\t");
      else if (code === 0x0a) out.push("\\n");
      else if (code === 0x0c) out.push("\\f");
      else if (code === 0x0d) out.push("\\r");
      else if (code < 0x20) out.push("\\u" + code.toString(16).padStart(4, "0"));
      else out.push(ch);
    }
    out.push('"');
    return out.join("");
  }

  function jcsValue(v) {
    if (v === null || v === undefined) return "null";
    if (v === true) return "true";
    if (v === false) return "false";
    if (typeof v === "string") return jcsString(v);
    if (typeof v === "number") {
      if (!Number.isFinite(v) || Math.floor(v) !== v) {
        throw new Error("non-integer number in a digest-bearing field");
      }
      return String(v);
    }
    if (Array.isArray(v)) return "[" + v.map(jcsValue).join(",") + "]";
    if (typeof v === "object") {
      var keys = Object.keys(v).sort(function (a, b) {
        return a < b ? -1 : a > b ? 1 : 0;
      });
      return (
        "{" +
        keys
          .map(function (k) {
            return jcsString(k) + ":" + jcsValue(v[k]);
          })
          .join(",") +
        "}"
      );
    }
    throw new Error("unsupported value in digest-bearing field");
  }

  function utf8Bytes(s) {
    return new TextEncoder().encode(s);
  }

  async function sha256Hex(bytes) {
    var digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.prototype.map
      .call(new Uint8Array(digest), function (b) {
        return b.toString(16).padStart(2, "0");
      })
      .join("");
  }

  async function jsonDigest(v) {
    return sha256Hex(utf8Bytes(jcsValue(normalize(v))));
  }

  async function computeCapsuleId(capsule) {
    var canonical = {};
    Object.keys(capsule).forEach(function (k) {
      if (k !== "capsule_id" && k !== "chain") canonical[k] = capsule[k];
    });
    return jsonDigest(canonical);
  }

  // ---- fragment encode/decode (mirrors report/render.py) ----

  function decodeFragment(hash) {
    var token = (hash || "").replace(/^#/, "");
    if (!token) return null;
    var b64 = token.replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return JSON.parse(new TextDecoder("utf-8").decode(bytes));
  }

  function encodeFragment(payload) {
    var bytes = utf8Bytes(JSON.stringify(payload));
    var binary = Array.prototype.map
      .call(bytes, function (b) {
        return String.fromCharCode(b);
      })
      .join("");
    var b64 = btoa(binary);
    return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  // ---- money formatting (mirrors report/build.py) ----

  var CURRENCY_SYMBOLS = { USD: "$", EUR: "€", GBP: "£" };

  function formatCompactMoney(amountMinor, currency) {
    var symbol = CURRENCY_SYMBOLS[currency] || (currency ? currency + " " : "");
    var major = amountMinor / 100;
    if (Math.abs(major) >= 1000) {
      return symbol + (major / 1000).toFixed(1) + "k";
    }
    return symbol + major.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function valueHeldLabel(rows) {
    var totals = {};
    rows.forEach(function (r) {
      if (r.amount_minor !== null && r.amount_minor !== undefined) {
        var cur = r.currency || "";
        totals[cur] = (totals[cur] || 0) + r.amount_minor;
      }
    });
    var keys = Object.keys(totals).sort();
    if (!keys.length) return "no amount carried by these actions";
    return keys.map(function (k) { return formatCompactMoney(totals[k], k); }).join(" + ");
  }

  function shortId(id) {
    return id ? id.slice(0, 8) + "…" : "";
  }

  // ---- redaction (display fields only -- cited capsule bodies travel
  // byte-for-byte so their own digest stays checkable after redaction) ----

  var MONEY_RE = /[$€£]\s?[\d][\d,.]*/g;
  var ID_RE = /\b[A-Z]{2,}-\d{2,}\b|\b[A-Z]{2}\d{2}[A-Z0-9]{6,}\b/g;

  async function sealToken(value) {
    var hex = await sha256Hex(utf8Bytes(String(value)));
    return "sealed:" + hex.slice(0, 12) + "…";
  }

  async function redactText(text) {
    var tokens = [];
    (text.match(MONEY_RE) || []).forEach(function (t) { tokens.push(t); });
    (text.match(ID_RE) || []).forEach(function (t) { tokens.push(t); });
    var seen = {};
    var result = text;
    for (var i = 0; i < tokens.length; i++) {
      var token = tokens[i];
      if (seen[token]) continue;
      seen[token] = true;
      var sealed = await sealToken(token);
      result = result.split(token).join("[" + sealed + "]");
    }
    return result;
  }

  async function redactPayload(payload) {
    var clone = JSON.parse(JSON.stringify(payload));
    var agentSeals = {};
    for (var g = 0; g < clone.guards.length; g++) {
      var rows = clone.guards[g].rows;
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        if (!agentSeals[row.agent]) agentSeals[row.agent] = await sealToken(row.agent);
        row.agent = agentSeals[row.agent];
        row.why = await redactText(row.why);
      }
    }
    clone.operator = await sealToken(clone.operator);
    clone.agents = clone.agents.map(function (_, idx) {
      return "sealed-agent-" + idx;
    });
    clone.redacted = true;
    return clone;
  }

  // ---- DOM rendering ----

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function setText(sel, text, root) {
    var el = $(sel, root);
    if (el) el.textContent = text;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function flattenRows(payload) {
    var out = [];
    payload.guards.forEach(function (section) {
      section.rows.forEach(function (row) {
        out.push({ section: section, row: row });
      });
    });
    return out;
  }

  function renderHeadline(payload, allRows) {
    setText("[data-headline-replayed]", String(payload.actions_replayed));
    setText(
      "[data-headline-replayed-envelope]",
      "records " + payload.record_range[0] + "–" + payload.record_range[1] + " · ckpt #" + payload.checkpoint
    );
    setText("[data-headline-held]", String(allRows.length));
    var triggered = Array.from(new Set(allRows.map(function (x) { return x.section.id; })));
    setText(
      "[data-headline-held-envelope]",
      String(triggered.length) + (triggered.length === 1 ? " guard" : " guards") + " · named reasons below"
    );
    setText("[data-headline-value]", valueHeldLabel(allRows.map(function (x) { return x.row; })));
  }

  function renderMeta(payload) {
    setText("[data-operator]", payload.operator || "—");
    setText("[data-agent-count]", String((payload.agents || []).length));
    setText("[data-record-range]", payload.record_range[0] + "–" + payload.record_range[1]);
    setText("[data-checkpoint]", String(payload.checkpoint));
    setText("[data-verify-command]", (payload.replay_command || "").replace("--share", "--verify"));
    setText("[data-enforce-count]", String(flattenRows(payload).length));
  }

  function renderGuardSections(payload) {
    var container = $("[data-guard-sections]");
    container.innerHTML = "";
    var rowTemplate = $("#row-template");
    var sectionTemplate = $("#section-template");
    payload.guards.forEach(function (section) {
      if (!section.rows.length) return;
      var node = sectionTemplate.content.firstElementChild.cloneNode(true);
      setText("[data-guard-id]", section.id, node);
      setText("[data-guard-what]", section.what, node);
      setText("[data-guard-count]", String(section.rows.length) + " would-have-held", node);
      var table = $("[data-guard-table]", node);
      section.rows.forEach(function (row, idx) {
        var rowNode = rowTemplate.content.firstElementChild.cloneNode(true);
        setText("[data-row-when]", row.when, rowNode);
        setText("[data-row-agent]", row.agent, rowNode);
        setText("[data-row-why]", row.why, rowNode);
        setText("[data-row-fp]", shortId(row.capsule && row.capsule.capsule_id), rowNode);
        if (idx % 2 === 1) rowNode.style.background = "rgba(122,140,110,0.05)";
        table.appendChild(rowNode);
      });
      container.appendChild(node);
    });
  }

  function renderConsequential(payload) {
    var wrap = $("[data-consequential]");
    if (!payload.consequential) {
      wrap.hidden = true;
      return;
    }
    var section = payload.guards.filter(function (s) { return s.id === payload.consequential.guard_id; })[0];
    var row = section && section.rows[payload.consequential.row_index];
    if (!row) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    var body = row.when + " — " + row.agent + " " + row.why + ".";
    if (section.id === "dedupe" && row.cited_capsule_id) {
      body += " Both carry the same order fingerprint — the dedupe guard matches them byte-for-byte.";
    }
    setText("[data-consequential-body]", body);
    var linksWrap = $("[data-consequential-links]");
    linksWrap.innerHTML = "";
    if (row.cited_capsule_id) {
      linksWrap.appendChild(el("span", "fp-link", "first payment " + shortId(row.cited_capsule_id)));
      linksWrap.appendChild(document.createTextNode(" · "));
      linksWrap.appendChild(el("span", "fp-link", "duplicate " + shortId(row.capsule.capsule_id)));
    } else {
      linksWrap.appendChild(el("span", "fp-link", "record " + shortId(row.capsule.capsule_id)));
    }
  }

  function renderModelNote(payload) {
    var wrap = $("[data-model-note-row]");
    if (!payload.model_note) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    setText("[data-model-quote]", '“' + payload.model_note.quote + '”');
    setText(
      "[data-model-byline]",
      "drafted by " + payload.model_note.model_id + ", reading the " + payload.model_note.record_count + " records above · interpretation, not evidence"
    );
    setText("[data-tuning-recheck]", "capsule guard dry-run --since " + (payload.since || "7d") + " --share");
  }

  function renderPermalink(payload) {
    var href = location.href;
    var shown = href.length > 72 ? href.slice(0, 72) + "…" : href;
    setText("[data-permalink]", shown);
    var disclosure = $("[data-disclosure]");
    if (disclosure) disclosure.hidden = false;
  }

  async function verifyAll(payload) {
    var capsules = [];
    flattenRows(payload).forEach(function (x) {
      if (x.row.capsule) capsules.push(x.row.capsule);
      if (x.row.cited_capsule) capsules.push(x.row.cited_capsule);
    });
    var mismatches = [];
    for (var i = 0; i < capsules.length; i++) {
      var c = capsules[i];
      if (!c || !c.capsule_id) continue;
      try {
        var recomputed = await computeCapsuleId(c);
        if (recomputed !== c.capsule_id) mismatches.push(c.capsule_id);
      } catch (e) {
        mismatches.push(c.capsule_id || "(unknown)");
      }
    }
    return { ok: mismatches.length === 0, mismatches: mismatches, checked: capsules.length };
  }

  function setVerifiedBadge(payload, result) {
    var badge = $("[data-verified-badge]");
    if (!badge) return;
    var foldLabel = "fold guard_dry_run@" + payload.checkpoint;
    var status;
    if (result.checked === 0) {
      status = "no cited records to verify";
    } else if (result.ok) {
      status = "verified on open ✓";
    } else {
      status = "recompute differs from " + result.mismatches.length + " cited record(s) — the ledger changed";
    }
    badge.textContent = foldLabel + " · " + status;
    badge.classList.toggle("mismatch", result.checked > 0 && !result.ok);
  }

  var currentPayload = null;

  async function renderFromPayload(payload) {
    currentPayload = payload;
    var allRows = flattenRows(payload);
    $("[data-empty-state]").hidden = true;
    $("[data-card]").hidden = false;
    $("[data-below-card]").hidden = false;
    renderHeadline(payload, allRows);
    renderMeta(payload);
    renderGuardSections(payload);
    renderConsequential(payload);
    renderModelNote(payload);
    renderPermalink(payload);
    var result = await verifyAll(payload);
    setVerifiedBadge(payload, result);
  }

  async function main() {
    var payload = null;
    try {
      payload = decodeFragment(location.hash);
    } catch (e) {
      payload = null;
    }
    if (!payload) {
      $("[data-empty-state]").hidden = false;
      $("[data-card]").hidden = true;
      $("[data-below-card]").hidden = true;
      return;
    }
    await renderFromPayload(payload);
  }

  document.addEventListener("DOMContentLoaded", function () {
    main();

    var copyBtn = $("[data-copy-permalink]");
    if (copyBtn) {
      copyBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(location.href).then(function () {
            var original = copyBtn.textContent;
            copyBtn.textContent = "copied";
            setTimeout(function () { copyBtn.textContent = original; }, 1200);
          });
        }
      });
    }

    var redactBtn = $("[data-redact]");
    if (redactBtn) {
      redactBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (!currentPayload) return;
        redactPayload(currentPayload).then(function (redacted) {
          var frag = encodeFragment(redacted);
          history.replaceState(null, "", location.pathname + location.search + "#" + frag);
          return renderFromPayload(redacted);
        });
      });
    }
  });

  // exposed for tests / --verify self-checks run from a headless JS shell
  window.__dryRunReport = {
    normalize: normalize,
    jcsValue: jcsValue,
    computeCapsuleId: computeCapsuleId,
    decodeFragment: decodeFragment,
    encodeFragment: encodeFragment,
    valueHeldLabel: valueHeldLabel,
  };
})();
