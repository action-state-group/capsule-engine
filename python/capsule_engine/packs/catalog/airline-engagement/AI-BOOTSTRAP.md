# AI-BOOTSTRAP: airline-engagement

Paste everything below the line into your AI coding assistant, in the repo
that runs a judge (`capsule_compiler.judge_agent`) over airline-support-style
agent conversations you want covered by the `airline-engagement` pack.

---

You are helping me wire `capsule-engine`'s `airline-engagement` starter pack
into this codebase. Unlike `payments-safety`, this is a JUDGE/REPORT-time
pack, not a gate-time enforcement pack: its payload is `outcomes[]` -- seven
terms (A1-A7) a judge run scores per closed conversation, not a live
obligation your dispatch path must satisfy before acting.

**Every outcome's `measurability` field tells you what to wire:**

- `measurability: measured` (the default) -- this term needs a real
  per-unit check. Bind it to a `capsule_compiler.judge_agent.TermSpec` with
  `executor_class: DETERMINISTIC` (or `MODEL_ASSISTED` if your corpus can
  support a live judge) and an `applicability` that reflects this pack's
  `evidence_rule` for that outcome.
- `measurability: declared_not_measured` -- this term is honestly
  inapplicable on a corpus that never emits the structured record its
  `evidence_instrument` names (e.g. a typed severity/efficacy label, a
  restriction-reason-cited record). Wire this term's `TermSpec.applicability`
  to always return `False` -- but do NOT do this for a term this pack
  declares `measured`; that is exactly the trust boundary
  `capsule_engine.packs.corpus_verify.verify_declared_not_measured` polices.

**Before sealing a single verdict, run the oracle check:**

```python
from pathlib import Path

import capsule_engine.packs
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.corpus_verify import verify_declared_not_measured

pack = load_pack_dir(Path(capsule_engine.packs.__file__).parent / "catalog" / "airline-engagement")
verify_declared_not_measured(pack, corpus)  # corpus: iterable of {"messages": [...]} unit dicts
```

This raises `CorpusVerificationError` if any `declared_not_measured`
outcome's `evidence_instrument` actually resolves on your corpus -- meaning
either your corpus DOES carry that record (re-declare the outcome as
`measured` and wire a real check) or a coder pointed a real, measurable term
at the `declared_not_measured` path to hide a fail. Either way, do not
silently swallow this error.
