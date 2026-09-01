// SPDX-License-Identifier: Apache-2.0
//
// capsule_viewer.js -- the BASE viewer's shared shell + the domain-module
// plug-in seam.
//
// This is the load-bearing base half of the fragment-carried viewer: it owns
// exactly the machinery that is the SAME for every capsule kind, and delegates
// only the per-record CARD BODY to a registered domain renderer. Concretely the
// base owns:
//
//   1. Fragment carry -- reads the payload from `window.__CAPSULE_FRAGMENT_B64U__`
//      (the self-contained embed) or `location.hash` (an explicit permalink).
//      A browser never sends the fragment over the wire, so a hosted copy of the
//      page never receives the capsules.
//   2. In-browser capsule_id recompute -- the canonicalization below is the SAME
//      hand-port of agent_action_capsule/canonical.py's vintage format-2
//      construction that report/static/verify.js ships (drop {capsule_id, chain},
//      absent-field normalize, RFC 8785 JCS, SHA-256). It is NOT forked per
//      module: every card, whatever its kind, gets its id recomputed here and
//      the ✓/✗ chip stamped by the base.
//   3. The page skeleton (empty-state, meta line, permalink chrome) and the ONE
//      "show the security checks" toggle wrapper around each card.
//
// A DOMAIN MODULE (e.g. conversation_exchange_card.js) supplies ONLY a card-body
// renderer and registers it by capsule kind:
//
//     CapsuleViewer.register("conversation_exchange", renderConversationExchangeCard);
//
// The base dispatches per record on `entry.record.asg_payload.event` (the
// capsule kind / payload-type), calls the registered renderer to get the card
// body, then wraps it with the shared recompute chip + security toggle. A module
// that catches itself needing canonicalization or fragment logic is doing the
// base's job -- it should read `entry.recompute` (the base already ran it)
// instead of re-porting the digest. The mesh role/question view can become a
// second module on this exact seam later with no change here.
(function () {
  "use strict";

  // ---- canonicalization (mirrors agent_action_capsule/canonical.py, and is
  // byte-for-byte the same port report/static/verify.js uses) --------------

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

  // Vintage format-2 construction: drop {capsule_id, chain}; the local-only
  // envelope fields (top-level signature/key_id) are dropped too. These
  // conversation_exchange capsules seal the signature UNDER `asg_signature`
  // BEFORE computing the id, so `asg_signature` is deliberately KEPT (it is part
  // of the preimage), while a legacy top-level signature/key_id is not.
  async function recomputeCapsuleId(capsule) {
    var excluded = { capsule_id: 1, chain: 1, signature: 1, key_id: 1 };
    var canonical = {};
    Object.keys(capsule).forEach(function (k) {
      if (!excluded[k]) canonical[k] = capsule[k];
    });
    return jsonDigest(canonical);
  }

  // ---- fragment decode (mirrors report/render.py base64url, no padding) ---

  function decodeFragment(token) {
    token = (token || "").replace(/^#/, "");
    if (!token) return null;
    var b64 = token.replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return JSON.parse(new TextDecoder("utf-8").decode(bytes));
  }

  // ---- the plug-in seam: a registry keyed by capsule kind ----------------

  var REGISTRY = {};

  // register(kind, renderCard): a domain module supplies ONE function that,
  // given `(entry, helpers)`, returns a DOM node for the card BODY (the
  // words-first, kind-specific content). It never touches canonicalization,
  // fragment carry, or the security-toggle wrapper -- the base owns those.
  function register(kind, renderCard) {
    if (typeof renderCard !== "function") {
      throw new Error("register(" + kind + "): renderCard must be a function");
    }
    REGISTRY[kind] = renderCard;
  }

  function kindOf(record) {
    return (record && record.asg_payload && record.asg_payload.event) || null;
  }

  // Small DOM helpers handed to every module so cards stay consistent and no
  // module re-implements them.
  var helpers = {
    el: function (tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    },
    shortId: function (id) {
      return id ? id.slice(0, 12) + "…" : "(no id)";
    },
  };

  // ---- per-record dispatch + shared wrapper ------------------------------

  async function renderEntry(entry) {
    // 1) The base recomputes capsule_id ONCE and hands the result to the card.
    var recompute = { id: entry.capsule_id, recomputed: null, matches: null };
    try {
      recompute.recomputed = await recomputeCapsuleId(entry.record);
      recompute.matches = recompute.recomputed === entry.capsule_id;
    } catch (e) {
      recompute.matches = null;
    }
    entry.recompute = recompute;

    var wrapper = helpers.el("div", "entry");

    // 2) The shared recompute chip -- stamped by the base, identical for every
    //    kind. Plain default; the crypto detail lives behind the toggle below.
    var head = helpers.el("div", "entry-head");
    var kind = kindOf(entry.record);
    var renderCard = REGISTRY[kind];
    head.appendChild(helpers.el("span", "entry-kind", kind || "(unknown kind)"));
    var chip = helpers.el("span", "recompute-chip");
    if (recompute.matches === true) {
      chip.className = "recompute-chip ok";
      chip.textContent = "✓ capsule_id verified in your browser";
    } else if (recompute.matches === false) {
      chip.className = "recompute-chip fail";
      chip.textContent = "✗ capsule_id MISMATCH — the record changed";
    } else {
      chip.className = "recompute-chip";
      chip.textContent = "— id not recomputable";
    }
    head.appendChild(chip);
    wrapper.appendChild(head);

    // 3) Delegate the card BODY to the registered domain module.
    if (renderCard) {
      var body = await renderCard(entry, helpers);
      if (body) wrapper.appendChild(body);
    } else {
      wrapper.appendChild(
        helpers.el(
          "div",
          "no-renderer",
          "No domain module is registered for capsule kind “" + (kind || "?") + "”."
        )
      );
    }

    // 4) The ONE shared "show the security checks" toggle -- sealed digests +
    //    the raw recompute, collapsed by default, identical for every kind.
    var details = document.createElement("details");
    details.className = "checks";
    var summary = document.createElement("summary");
    summary.textContent = "Show the security checks";
    var hint = helpers.el("span", "hint", " — recomputed id, sealed digests");
    summary.appendChild(hint);
    details.appendChild(summary);
    var checksBody = helpers.el("div", "checks-body");
    var lines = [];
    lines.push("capsule_id: " + (entry.capsule_id || "(none)"));
    lines.push("recomputed in-browser: " + (recompute.recomputed || "(not recomputable)"));
    lines.push(
      "id matches: " +
        (recompute.matches === true ? "yes" : recompute.matches === false ? "NO" : "n/a")
    );
    var ma = (entry.record && entry.record.model_attestation) || {};
    var ca = ma.compute_attestation || {};
    ["agent_input_digest", "agent_output_digest", "tool_calls_digest", "reasoning_digest"].forEach(
      function (k) {
        if (ca[k]) lines.push(k + ": " + ca[k]);
      }
    );
    var sig = entry.record && entry.record.asg_signature;
    if (sig) lines.push("signature: " + sig.alg + " / " + sig.key_id + " / " + (sig.sig || "").slice(0, 16) + "…");
    var idLine = helpers.el("div", "id-line");
    idLine.textContent = lines.join("\n");
    checksBody.appendChild(idLine);
    details.appendChild(checksBody);
    wrapper.appendChild(details);

    return wrapper;
  }

  // ---- boot: fill the skeleton, dispatch every entry ---------------------

  async function boot() {
    var payload = null;
    try {
      var UNFILLED = "@@" + "FRAGMENT" + "@@";
      var embedded = (typeof window !== "undefined" && window.__CAPSULE_FRAGMENT_B64U__) || "";
      if (embedded && embedded !== UNFILLED) payload = decodeFragment(embedded);
      var hash = location.hash.slice(1);
      if (hash) payload = decodeFragment(hash); // an explicit #fragment wins
    } catch (e) {
      payload = null;
    }
    if (!payload || !payload.entries) return; // empty-state stays shown

    var empty = document.querySelector("[data-empty]");
    if (empty) empty.hidden = true;

    var meta = document.querySelector("[data-meta]");
    if (meta) {
      meta.textContent =
        (payload.operator ? "operator " + payload.operator + " · " : "") +
        payload.entries.length +
        " capsule(s)" +
        (payload.source ? " · " + payload.source : "");
    }

    var container = document.querySelector("[data-entries]");
    for (var i = 0; i < payload.entries.length; i++) {
      container.appendChild(await renderEntry(payload.entries[i]));
    }

    var permalink = document.querySelector("[data-permalink]");
    if (permalink) permalink.textContent = location.href;
    var copy = document.querySelector("[data-copy]");
    if (copy) {
      copy.addEventListener("click", function () {
        if (navigator.clipboard) navigator.clipboard.writeText(location.href);
        copy.textContent = "copied";
        setTimeout(function () {
          copy.textContent = "copy permalink";
        }, 1500);
      });
    }
  }

  // ---- expose the base API: the seam + the recompute port ----------------

  var CapsuleViewer = {
    register: register,
    recomputeCapsuleId: recomputeCapsuleId,
    jsonDigest: jsonDigest,
    jcsValue: jcsValue,
    normalize: normalize,
    decodeFragment: decodeFragment,
    boot: boot,
    _registry: REGISTRY,
  };
  if (typeof window !== "undefined") {
    window.CapsuleViewer = CapsuleViewer;
    window.__capsuleViewerBoot = boot;
  }

  // Boot after the DOM AND every module script (which run synchronously after
  // this one in the shell) have registered. DOMContentLoaded guarantees both.
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})();
