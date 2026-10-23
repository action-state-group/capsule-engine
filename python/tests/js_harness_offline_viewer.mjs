// SPDX-License-Identifier: Apache-2.0
// Node harness for the literal "opens and verifies with no network" acceptance
// test (tests/test_bundle_with_viewer.py). Extracts the actual inline
// <script> blocks from a real `capsule bundle --with-viewer` HTML output --
// not a reimplementation -- and runs them in file order via
// vm.runInThisContext, exactly like a browser parsing top-to-bottom would:
// the embed-slot script sets window.__BUNDLE_FRAGMENT_B64U__, then MMR_JS,
// then BUNDLE_JS, whose own IIFE calls bootstrapLoad() automatically at the
// bottom -- the same automatic call a real page load triggers.
//
// Every network-capable global (fetch, XMLHttpRequest, WebSocket) is
// replaced with a function that throws before any of that code runs, so if
// the automatic load-and-verify path -- or the exposed pure verification
// functions this script also calls afterward -- ever attempted a network
// call, this harness would fail loudly instead of silently succeeding.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const htmlPath = process.argv[2];
// Strip HTML comments first -- the vendored template's own provenance
// comment mentions "<script>" in prose, which would otherwise confuse a
// naive scan for real <script> tags.
const html = readFileSync(htmlPath, "utf8").replace(/<!--[\s\S]*?-->/g, "");

const scriptTagRe = /<script([^>]*)>([\s\S]*?)<\/script>/g;
const scripts = [];
let m;
while ((m = scriptTagRe.exec(html)) !== null) {
  const attrs = m[1];
  if (/\bsrc\s*=/.test(attrs)) {
    throw new Error("offline viewer must not have any <script src=...> tag -- found one: " + attrs);
  }
  scripts.push(m[2]);
}
if (scripts.length !== 3) {
  throw new Error("expected exactly 3 inline <script> blocks (embed slot, MMR_JS, BUNDLE_JS), found " + scripts.length);
}

// ---- network hard-block: any attempt throws before touching the network ----
const networkAttempts = [];
function blocked(name) {
  return function (...args) {
    networkAttempts.push({ name, args: String(args[0]) });
    throw new Error("network attempted via " + name + "(" + String(args[0]) + ") -- must never happen offline");
  };
}
globalThis.fetch = blocked("fetch");
globalThis.XMLHttpRequest = function () { throw new Error("network attempted via XMLHttpRequest"); };
globalThis.WebSocket = function () { throw new Error("network attempted via WebSocket"); };

// ---- minimal DOM/BOM shim -- only what BUNDLE_JS actually touches (see
// this file's own grep of the vendored source for the exact surface) ----
function makeElementStub() {
  const store = { style: {}, classList: { add() {}, remove() {}, contains: () => false, toggle() {} } };
  return new Proxy(store, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (["addEventListener", "removeEventListener", "click", "appendChild", "removeChild",
           "setAttribute", "focus", "blur", "remove"].includes(prop)) {
        return () => {};
      }
      if (prop === "getAttribute") return () => null;
      if (prop === "querySelectorAll") return () => [];
      if (prop === "querySelector") return () => null;
      return undefined;
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
}

const elements = {};
globalThis.window = globalThis;
globalThis.document = {
  body: makeElementStub(),
  getElementById(id) {
    if (!elements[id]) elements[id] = makeElementStub();
    return elements[id];
  },
  createElement() {
    return makeElementStub();
  },
  querySelectorAll() {
    return [];
  },
  addEventListener() {},
};
globalThis.location = {
  hash: "", pathname: "/tmp/offline-bundle.html", search: "",
  origin: "file://", protocol: "file:",
};
globalThis.history = { replaceState() {} };
// Node 21+ ships a partial, getter-only `navigator` global already -- no
// shim needed (BUNDLE_JS only reads `navigator.clipboard`, guarded by an
// `if`, and never calls it in the code path this harness exercises).

let loadError = null;
try {
  for (const src of scripts) {
    vm.runInThisContext(src, { filename: "inline-script.js" });
  }
} catch (e) {
  loadError = String((e && e.stack) || e);
}

async function main() {
  const result = { loadError, networkAttempts };

  if (!loadError) {
    // The embed-slot script set this; extract it back out the same way
    // bootstrapLoad() itself reads it, so the fragment driving the direct
    // function calls below is the literal one embedded in the file, not a
    // value this harness invented.
    const fragment = globalThis.window.__BUNDLE_FRAGMENT_B64U__;
    result.fragmentEmbedded = typeof fragment === "string" && fragment !== "@@BUNDLE_FRAGMENT@@";

    const bundle = decodeFragment(fragment);
    const records = bundle.records || [];
    const privlog = await buildBundlePrivlog(records);
    const completeness = await checkCompleteness(bundle);
    const crossCheck = await crossCheckSelfReport(bundle, records);
    const ritual = await evaluateBundleRitual(records, completeness, crossCheck);

    result.recordCount = records.length;
    result.privlogCount = privlog.length;
    result.completeness = completeness;
    result.crossCheck = crossCheck;
    result.ritual = ritual;
  }

  process.stdout.write(JSON.stringify(result));
}

main().catch((e) => {
  process.stderr.write(String((e && e.stack) || e));
  process.exit(1);
});
