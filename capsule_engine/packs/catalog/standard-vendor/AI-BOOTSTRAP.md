# AI-BOOTSTRAP: standard-vendor

Paste everything below the line into your AI coding assistant, in the repo
that runs a judge (`capsule_compiler.judge_agent`) over your own agent's
sealed ledger and wants a standard, vendor-neutral coverage read on it.

---

You are helping me wire `capsule-ledger`'s `standard-vendor` pack into this
codebase. Unlike `airline-engagement`, this pack is NOT domain voice: every
outcome is phrased against capsule shapes ANY emit layer already produces
(a read chained to a write, a citation digest, a stated constraint the
counterparty emitted) -- so it grades a conversational agent, a read-only
investigator, a coding agent, or a sales agent with the same 22 rows.

**Rows are organized by `mode` (schema.MODE_VALUES), not by domain:**

- `structural` (S1-S4) -- presence/absence over emitted fields, no model.
- `value` (V1-V2) -- arithmetic over sealed emitted numbers, no model.
- `judged` (J1-J6) -- an LLM reads your sealed prose against a
  digest-pinned prompt; bind these to a `capsule_compiler.judge_agent
  .TermSpec` with `executor_class: MODEL_ASSISTED`.
- `fold_rollup` (F1) -- a per-session job-success rollup over this pack's
  own `must_have`-tier rows (§8.4); no new judge call.
- `fold_counterparty` (C1-C6) -- the differentiated value-props: each is a
  fold over a per-session signal, min-N gated, correlation-not-cause
  framed. Wiring the underlying signal declarations is
  `[ldg-bp-counterparty-change-family]`, not this pack alone.
- `fold_agent` (T1-T2) / `fold_cohort` (X1) -- trajectory and cohort-
  comparison folds over the same rollup, no new judge call.

**`tier` says whether a row gates job success:** `must_have` rows feed
F1's rollup; `informational` rows (the default) are reported but never
gate. No per-term target/ratio is declared -- tier only (design §8.2).

**Every row's `measurability` field tells you what to wire, same
convention as `airline-engagement`:**

- `measurability: measured` (the default) -- bind it to a real per-unit
  check (a `judged`-mode row needs only the sealed free text every corpus
  already carries; a `structural`/`value` row needs the typed field its
  `evidence_rule` names).
- `measurability: declared_not_measured` -- this term is honestly
  inapplicable on a corpus that never emits the structured record its
  `evidence_instrument` names. Wire this term's `TermSpec.applicability`
  to always return `False` -- but do NOT do this for a term this pack
  declares `measured`; that is exactly the trust boundary
  `capsule_ledger.packs.corpus_verify.verify_declared_not_measured`
  polices.

**Before sealing a single verdict, run the oracle check:**

```python
from capsule_ledger.packs.loader import load_pack_dir
from capsule_ledger.packs.corpus_verify import verify_declared_not_measured

pack = load_pack_dir("capsule_ledger/packs/catalog/standard-vendor")
verify_declared_not_measured(pack, corpus)  # corpus: iterable of unit dicts
```

This raises `CorpusVerificationError` if any `declared_not_measured`
outcome's `evidence_instrument` actually resolves on your corpus -- meaning
your corpus DOES carry that record (re-declare the outcome as `measured`
and wire a real check).

**A vendor does not have to satisfy every row.** `propose`-style grading
(design §1) runs this pack backward over your ledger and returns a
coverage map: per row, N-of-M held or WITH-INSTRUMENTATION naming exactly
the field you would need to emit to unlock it. Adopt the subset your emit
layer supports today; the report names the rest.
