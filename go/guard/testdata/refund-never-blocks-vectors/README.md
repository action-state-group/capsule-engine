# Go vectors: a refund never blocks the spend window

Engine: capsule-engine branch `refund-never-blocks-spend-window` (base 8522efcd714a6ba96de800e6c7ded5593e928d2d).
Regenerate: from that checkout, `python -P <this dir>/generate_python_decisions.py <this dir>`.

## What the engine has, and does not have
- The Python engine has NO history_scope window coverage (no equivalent of Go `windowCoverage`). The
  "N earlier act(s) carry no recorded decision" not_evaluable exists only in the Go port
  (go/guard/engine.go). So F1 (the false not_evaluable + "purchase made without a
  check" card) is a Go-only defect; the Python replay never produced it. The engine side here names the rule
  (`capsule_engine.report.replay.MONEY_IN = "in"`) and pins the arithmetic with tests.
- Bridge rule (unchanged behaviour, now one constant): a deal check whose body says `direction: "in"` is
  bridged with spend (`amount_minor`) 0, whatever `spend_minor` says. See `bridge` in python-decisions.json
  (spend_minor 500 -> amount_minor 0, refund and partial cancel).

## The Go change this asks for
In `windowCoverage`, an act with `Decision == nil` whose record states `direction: "in"` is NOT counted
as undecided (it cannot add to the window, so it cannot evade the cap; the engine-side ruling on F1). Every other
undecided act still makes the window not_evaluable (ruling (b) unchanged). Key on the direction, as the
bridge does, not on spend 0: an undecided OUT act with spend_minor missing/0 still blocks.
Evidence naming the excluded kinds (acceptance): when one or more money-in acts were skipped, the caps
evidence/reason says so, e.g. `excluded_from_window: {"direction": "in", "count": N}` and reason text
"N earlier money-in act(s) (refund or cancel) were not counted: they are never spend". Python seals no
such field (no window coverage to report from), so this field is Go-only; if you want Python to carry it
too, that is a sealed-evidence (wire) change and needs its own item.

## Sequences (python-decisions.json `sequences`)
Live engine, spend.weekly/3.0.0, window limit per case, classes money.purchase / money.refund /
external_commitment.other all capped:
- purchase 300 -> money-in act (stated spend_minor 500) -> purchase 200
- under-limit (1000): pass/allow, pass/allow (weekly 300), pass/allow (weekly 300, projected 500)
- over-limit (499):  pass/allow, pass/allow (weekly 300), FAIL/ESCALATE (weekly 300, projected 500)
Go acceptance: the same sequence through CheckWithHistory with the money-in act in history WITHOUT a
decision gives the third step the same caps result (pass or fail), never not_evaluable, and weekly 300.
