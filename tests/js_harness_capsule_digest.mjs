// Minimal Node harness so tests can exercise report/static/verify.js's real
// canonicalization + digest logic directly (not a reimplementation of it in
// Python) -- reads one capsule JSON object on stdin, prints the recomputed
// capsule_id (agent_action_capsule.compute_capsule_id's client-side twin).
import { readFileSync } from "node:fs";

globalThis.window = globalThis;
globalThis.document = { addEventListener: () => {} };

const src = readFileSync(process.argv[2], "utf8");
new Function(src)();

const capsule = JSON.parse(readFileSync(0, "utf8"));
const digest = await globalThis.__dryRunReport.computeCapsuleId(capsule);
process.stdout.write(digest);
