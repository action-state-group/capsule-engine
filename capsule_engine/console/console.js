// SPDX-License-Identifier: Apache-2.0
//
// Client-side rendering for the local console (`capsule console`). Vanilla
// JS, no build step, no external script -- matches gallery.html's own "no
// network calls" discipline (see server.py's docstring): every fetch() call
// below targets a same-origin, relative "/api/..." path, never an external
// URL.
//
// Verdict-chip glyphs/classes below are the presentation-layer mapping from
// components.css's own comment ("distinct from the data-layer
// disposition.decision / verdict_class tokens -- do not conflate"): sourced
// from real disposition.verdict_class / assurance.effect_mode values, never
// invented. "blocked" always renders the sage slashed-circle (never brick,
// never red) per the product law components.css documents at its verdict-
// chip section.
(function () {
  "use strict";

  var state = { selectedId: null };

  function qs(id) { return document.getElementById(id); }

  function fetchJSON(url) {
    return fetch(url).then(function (res) { return res.json(); });
  }

  function currentFilters() {
    var filters = {};
    ["agent", "verdict", "since", "until"].forEach(function (key) {
      var value = qs("filter-" + key).value.trim();
      if (value) filters[key] = value;
    });
    return filters;
  }

  function buildQuery(filters) {
    var parts = [];
    Object.keys(filters).forEach(function (key) {
      parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(filters[key]));
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  // Presentation-layer verdict chip -- see this file's header comment.
  function verdictPresentation(disposition, assurance) {
    var verdictClass = (disposition && disposition.verdict_class) || null;
    var effectMode = (assurance && assurance.effect_mode) || null;

    if (verdictClass === "blocked") {
      return { css: "blocked", glyph: "⊘", label: "blocked" };
    }
    if (verdictClass === "hitl_dispatched") {
      return {
        css: "needs-decision",
        glyph: "?",
        label: "needs decision",
        subline: disposition.approver ? "waiting: " + disposition.approver : null,
      };
    }
    if (effectMode === "dispatched_unconfirmed") {
      return { css: "dispatched", glyph: "→", label: "dispatched · unconfirmed" };
    }
    if (effectMode === "confirmed" || verdictClass === "executed" || verdictClass === "confirmed") {
      return { css: "confirmed", glyph: "✓", label: "confirmed" };
    }
    // Honestly-unknown fallback: a runtime/system state this console does
    // not recognize, never a guess dressed up as one of the above.
    return { css: "errored", glyph: "!", label: verdictClass || "errored" };
  }

  function verdictChipHTML(disposition, assurance) {
    var v = verdictPresentation(disposition, assurance);
    var subline = v.subline ? '<span class="verdict-subline">' + escapeHTML(v.subline) + "</span>" : "";
    return (
      '<span class="verdict-chip verdict-chip--' + v.css + '">' +
      '<span class="verdict-glyph">' + v.glyph + "</span>" + escapeHTML(v.label) +
      "</span>" + subline
    );
  }

  function escapeHTML(text) {
    var div = document.createElement("div");
    div.textContent = String(text == null ? "" : text);
    return div.innerHTML;
  }

  // Convention-label rendering (design principle item 2): action_class is
  // never a hardcoded string here -- it's whatever record.action_class /
  // detail.action_class the server-side registry lookup (registry/conventions.py)
  // already resolved. Unknown ids render as-is, marked unregistered -- honest,
  // not an error, never omitted. Reuses the existing ".mono" utility
  // (components.css) rather than inventing a new class.
  function actionClassHTML(actionClass) {
    if (!actionClass) return "";
    var suffix = actionClass.registered ? "" : " (unregistered)";
    return '<span class="mono">' + escapeHTML(actionClass.label) + escapeHTML(suffix) + "</span>";
  }

  // Assurance-grade rendering (design principle item 3): self_attested
  // renders plain text, a badged grade (assurance_grade.badged, currently
  // only "anchored") wears the neutral .rung-chip pill.
  function assuranceGradeHTML(assuranceGrade) {
    if (!assuranceGrade) return "";
    if (!assuranceGrade.badged) {
      return '<span class="rung-plain mono">' + escapeHTML(assuranceGrade.grade) + "</span>";
    }
    return '<span class="rung-chip"><span class="rung-dot" style="width:8px;height:8px;background:var(--asg-sage)"></span>' +
      escapeHTML(assuranceGrade.grade) + "</span>";
  }

  // Resolve-at-read rendering (item 5a). Reuses the existing verify-stage
  // component verbatim (the same one the real cryptographic Verify section
  // below uses): a match is the honest "pass" stage plus the resolved
  // content in the same .inspector-sealed block "Sealed fields" already
  // uses; a mismatch reuses verify-stage--fail (brick) -- a corrupted/
  // tampered local payload copy is a genuine verification failure, the
  // same product law that reserves brick for real crypto failures already
  // covers this case, not a new exception. See cli/format.py's
  // format_resolved_payload for the same rule applied to the CLI.
  function resolvedPayloadHTML(resolved) {
    if (!resolved) return "";
    if (resolved.match) {
      return (
        '<div class="verify-stage verify-stage--pass"><span class="verify-stage-glyph">✓</span>' +
        '<span class="verify-stage-name">resolved ' + escapeHTML(resolved.label) + "</span>" +
        '<span class="verify-stage-detail">from your local payload store — not part of the record; ' +
        "digest recomputed live: match</span></div>" +
        '<div class="inspector-sealed">' + escapeHTML(JSON.stringify(resolved.content, null, 2)) + "</div>"
      );
    }
    return (
      '<div class="verify-stage verify-stage--fail"><span class="verify-stage-glyph">✕</span>' +
      '<span class="verify-stage-name">resolved ' + escapeHTML(resolved.label) + " mismatch</span>" +
      '<span class="verify-stage-detail">recomputed ' + escapeHTML(resolved.recomputed_digest) +
      " ≠ recorded " + escapeHTML(resolved.digest) +
      " — the local copy may be corrupted or tampered; treat this content as unverified</span></div>"
    );
  }

  function renderCheckpoint(data) {
    qs("checkpoint-line").textContent = data.line;
  }

  function renderCliEcho(text) {
    qs("cli-echo-bar").textContent = text;
  }

  function renderRecords(data) {
    var stream = qs("record-stream");
    stream.innerHTML = "";
    data.records.forEach(function (record) {
      var row = document.createElement("div");
      row.className = "record-row" + (record.capsule_id === state.selectedId ? " selected" : "");
      row.dataset.capsuleId = record.capsule_id;
      row.innerHTML =
        '<span class="record-fp mono">' + escapeHTML(record.fingerprint) + "</span>" +
        '<span class="record-main">' +
        '<span class="record-action">' + escapeHTML(record.action) + " · " + escapeHTML(record.agent) +
        (record.action_class ? " · " + actionClassHTML(record.action_class) : "") + "</span>" +
        '<span class="record-meta">' + escapeHTML(record.timestamp) + "</span>" +
        "</span>" +
        verdictChipHTML(record.disposition, record.assurance);
      row.addEventListener("click", function () { selectRecord(record.capsule_id); });
      stream.appendChild(row);
    });

    qs("console-stream-status").textContent =
      data.shown + " of " + data.total + " records shown (filtered view — the ledger itself is never filtered) · " +
      (data.gap_count ? data.gap_count + " chain gap(s) detected" : "sequence unbroken");

    renderCliEcho(data.cli_echo);
  }

  function renderInspectorEmpty() {
    qs("inspector").innerHTML = '<div class="inspector-empty">select a record to inspect it</div>';
  }

  function renderChainRow(entry) {
    var row = document.createElement("button");
    row.type = "button";
    row.className = "inspector-chain-row mono";
    row.textContent = entry.fingerprint + " · " + (entry.relation || "(no relation given)");
    row.addEventListener("click", function () { selectRecord(entry.capsule_id); });
    return row;
  }

  function renderInspector(detail) {
    var inspector = qs("inspector");
    inspector.innerHTML = "";

    var header = document.createElement("div");
    header.className = "inspector-section";
    header.innerHTML =
      '<div class="inspector-section-title">Identity</div>' +
      '<div class="envelope-line">capsule ' + escapeHTML(detail.fingerprint) + "</div>" +
      verdictChipHTML(detail.disposition, (detail.sealed || {}).assurance) +
      assuranceGradeHTML(detail.assurance_grade) +
      (detail.action_class ? '<div class="rung-plain">' + actionClassHTML(detail.action_class) + "</div>" : "");
    inspector.appendChild(header);

    // Checks that ran -- policy constraints, presentation-layer reuse of
    // the verdict-chip component (pass=confirmed, fail=blocked -- sage,
    // never brick: a held check is not a verification failure). Brick is
    // reserved for the real cryptographic verify() section below.
    var checksSection = document.createElement("div");
    checksSection.className = "inspector-section";
    var checksHTML = '<div class="inspector-section-title">Checks that ran</div>';
    if (!detail.checks.length) {
      checksHTML += '<div class="inspector-chain-empty">no checks recorded on this capsule</div>';
    } else {
      checksHTML += '<div class="inspector-check-list">';
      detail.checks.forEach(function (check) {
        var chipClass = check.result === "pass" ? "confirmed" : check.result === "fail" ? "blocked" : "dispatched";
        var glyph = check.result === "pass" ? "✓" : check.result === "fail" ? "⊘" : "–";
        checksHTML +=
          '<span class="verdict-chip verdict-chip--' + chipClass + '" title="' + escapeHTML(check.method || "") + '">' +
          '<span class="verdict-glyph">' + glyph + "</span>" + escapeHTML(check.id || "check") +
          "</span>";
      });
      checksHTML += "</div>";
    }
    checksSection.innerHTML = checksHTML;
    inspector.appendChild(checksSection);

    // Resolved payloads (item 5a) -- deliberately its own section, not
    // interleaved into "Checks that ran" above: that block's own product
    // law (verified by test_console_js_blocked_check_result_never_uses_verify_stage_fail)
    // is that a policy check failing is never rendered with the
    // verify-stage--fail (brick) treatment reserved for real cryptographic
    // verification. A resolved evidence digest MISMATCH is exactly that
    // real kind of failure (a corrupted/tampered local copy), so it reuses
    // verify-stage--fail here, in the one section allowed to.
    var resolvedHTML = resolvedPayloadHTML(detail.resolved_reason);
    detail.checks.forEach(function (check) {
      resolvedHTML += resolvedPayloadHTML(check.resolved_evidence);
    });
    if (resolvedHTML) {
      var resolvedSection = document.createElement("div");
      resolvedSection.className = "inspector-section";
      resolvedSection.innerHTML = '<div class="inspector-section-title">Resolved payloads</div>' + resolvedHTML;
      inspector.appendChild(resolvedSection);
    }

    // Real cryptographic verification -- the one legitimate brick use in
    // this panel, reusing the verify-ritual component verbatim.
    var verifySection = document.createElement("div");
    verifySection.className = "inspector-section";
    var verifyHTML = '<div class="inspector-section-title">Verify</div><div style="border:1px solid var(--asg-sand); border-radius:12px">';
    if (detail.verify.ok) {
      verifyHTML +=
        '<div class="verify-stage verify-stage--pass"><span class="verify-stage-glyph">✓</span>' +
        '<span class="verify-stage-name">verifies</span>' +
        '<span class="verify-stage-detail">digest, chain, and signature all check out</span></div>';
    } else if (detail.verify.findings.length) {
      detail.verify.findings.forEach(function (finding) {
        verifyHTML +=
          '<div class="verify-stage verify-stage--fail"><span class="verify-stage-glyph">✕</span>' +
          '<span class="verify-stage-name mono">' + escapeHTML(finding.code) + "</span>" +
          '<span class="verify-stage-detail">' + escapeHTML(finding.detail) + "</span></div>";
      });
    } else {
      verifyHTML +=
        '<div class="verify-stage verify-stage--skip"><span class="verify-stage-glyph">–</span>' +
        '<span class="verify-stage-name">verify</span>' +
        '<span class="verify-stage-detail">no verification result available</span></div>';
    }
    verifyHTML += "</div>";
    verifySection.innerHTML = verifyHTML;
    inspector.appendChild(verifySection);

    // Chain: what this record cites, and what cites it.
    var chainSection = document.createElement("div");
    chainSection.className = "inspector-section";
    chainSection.innerHTML = '<div class="inspector-section-title">Chain</div>';
    var citesWrap = document.createElement("div");
    if (detail.chain.cites) {
      var citeLabel = document.createElement("div");
      citeLabel.className = "inspector-chain-empty";
      citeLabel.textContent = detail.chain.cites.found ? "cites:" : "cites (not found in this ledger — a chain gap):";
      citesWrap.appendChild(citeLabel);
      if (detail.chain.cites.found) citesWrap.appendChild(renderChainRow(detail.chain.cites));
    } else {
      var noCites = document.createElement("div");
      noCites.className = "inspector-chain-empty";
      noCites.textContent = "cites: (none)";
      citesWrap.appendChild(noCites);
    }
    chainSection.appendChild(citesWrap);

    var citedByLabel = document.createElement("div");
    citedByLabel.className = "inspector-chain-empty";
    citedByLabel.style.marginTop = "8px";
    citedByLabel.textContent = detail.chain.cited_by.length ? "cited by:" : "cited by: (nothing yet)";
    chainSection.appendChild(citedByLabel);
    detail.chain.cited_by.forEach(function (entry) { chainSection.appendChild(renderChainRow(entry)); });
    inspector.appendChild(chainSection);

    // Fold strip: live fold values for this record's agent, in the exact
    // deterministic-mark + envelope-line pattern gallery.html's own
    // deterministic-class example uses.
    var foldSection = document.createElement("div");
    foldSection.className = "inspector-section";
    var foldHTML = '<div class="inspector-section-title">Fold strip</div>';
    if (!detail.fold_strip.length) {
      foldHTML += '<div class="inspector-chain-empty">no folds configured</div>';
    } else {
      detail.fold_strip.forEach(function (fold) {
        foldHTML +=
          '<div class="inspector-fold-row">' +
          '<div style="display:flex; align-items:baseline; gap:8px">' +
          '<span class="mono" style="color:var(--asg-sage); font-weight:600; font-size:13px">✓</span>' +
          '<span class="inspector-fold-value">' + escapeHTML(fold.fold_id) + ": " +
          '<strong class="mono" style="font-weight:500">' + escapeHTML(JSON.stringify(fold.result)) + "</strong></span>" +
          "</div>" +
          '<div class="envelope-line">' + escapeHTML(fold.envelope_line) + "</div>" +
          "</div>";
      });
    }
    foldSection.innerHTML = foldHTML;
    inspector.appendChild(foldSection);

    // Sealed fields -- the raw capsule, exactly as sealed.
    var sealedSection = document.createElement("div");
    sealedSection.className = "inspector-section";
    sealedSection.innerHTML =
      '<div class="inspector-section-title">Sealed fields</div>' +
      '<div class="inspector-sealed">' + escapeHTML(JSON.stringify(detail.sealed, null, 2)) + "</div>";
    inspector.appendChild(sealedSection);

    var echoSection = document.createElement("div");
    echoSection.className = "inspector-section inspector-cli-echo";
    echoSection.innerHTML = '<div class="cli-echo">' + escapeHTML(detail.cli_echo) + "</div>";
    inspector.appendChild(echoSection);
  }

  function selectRecord(capsuleId) {
    state.selectedId = capsuleId;
    Array.prototype.forEach.call(document.querySelectorAll(".record-row"), function (row) {
      row.classList.toggle("selected", row.dataset.capsuleId === capsuleId);
    });
    fetchJSON("/api/records/" + encodeURIComponent(capsuleId)).then(function (detail) {
      if (detail.error) return;
      renderInspector(detail);
    });
  }

  function refresh() {
    fetchJSON("/api/checkpoint").then(renderCheckpoint);
    fetchJSON("/api/records" + buildQuery(currentFilters())).then(renderRecords);
  }

  document.addEventListener("DOMContentLoaded", function () {
    qs("filter-apply").addEventListener("click", refresh);
    renderInspectorEmpty();
    refresh();
  });
})();
