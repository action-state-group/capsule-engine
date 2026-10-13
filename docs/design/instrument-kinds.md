# Naming a non-event with an evidence instrument

**Status: proposal for the maintainer to read and decide on. Nothing in
this note is implemented by it; it changes no code and no pack.**

Measured at capsule-engine `fd43d4329ea31d69e9bdd0a79f23f013c3615219`
(written `fd43d43` below).

## The question

A pack rule can declare `measurability: declared_not_measured`. It then
names an `evidence_instrument`: the signal its real check would need. The
oracle in `capsule_engine/packs/corpus_verify.py` scans a corpus and fails
the pack if that signal appears anywhere, so "this corpus never carries the
record this rule needs" is checked rather than asserted.

There are exactly two instrument kinds
(`capsule_engine/packs/schema.py:179` at `fd43d43`):

```python
EVIDENCE_INSTRUMENT_KINDS = frozenset({"structured_field", "tool_call_name"})
```

Both ask whether a signal is *present* on a message. Some things a rule
cares about are not present as anything: a process that exits with a
status and writes no record, or an action that was refused and left
nothing behind. The question is whether the engine needs a third
instrument kind for that, or whether the two kinds already cover it.

## Proposal: no third kind; the observer writes a field

A non-event is named by a field that the program running the agent and
recording its transcript (the harness, or observer) writes onto a message
in that transcript. For example, when the process under test exits with
status 7 and seals nothing, the harness appends a message carrying
`exit_code: 7`. The rule then names
`evidence_instrument: {kind: structured_field, field: exit_code}`.

This already works with the existing `structured_field` kind. In
`capsule_engine/packs/corpus_verify.py:48-59` at `fd43d43`,
`_resolves_structured_field` skips only the base message keys
(`role`, `content`, `tool_call_names`) and resolves on any other key that is
present on a message with a non-empty value. So a harness-written
`exit_code` resolves as soon as the harness writes it, and the oracle sees
it like any other field.

A third kind ("absence of a record", "exit status") would add nothing the
oracle can check. The oracle reads a transcript; it cannot observe a
process. Whatever such a kind resolved against, something would still have
to write it into the transcript first, and that something is the harness.
A third kind would only hide that step.

## Where the field must go

The field must be written on a **message**, not on the unit. The oracle
iterates `unit["messages"]` and reads keys on each message dict
(`corpus_verify.py:48-59`, and `resolves_instrument` below it). A field
written at the unit level, beside `messages`, never resolves. The oracle
then passes for the wrong reason: a rule that is in fact measurable would be
accepted as `declared_not_measured`, and nothing would flag it.

The comment above `EVIDENCE_INSTRUMENT_KINDS` (`schema.py:170` at
`fd43d43`) still says "a named key on a unit/message dict", which suggests
either placement works. Only the message placement does; that comment is
being corrected separately.

## What grade a claim resting on such a field carries

A harness-written field is a statement by the observer, not a record made
by the system under test. A claim that rests on it is graded
`self-attested`: the party that ran the test is the party saying what
happened. It can rise above `self-attested` only by the usual means for any
record (an independent party countersigning the transcript, or the field
being bound to a record the system under test itself signed), never by
being named through an instrument.

## Consequences if accepted

- `EVIDENCE_INSTRUMENT_KINDS` stays a closed set of two. So does its copy in
  `capsule_engine/register/loader.py:45`.
- A pack author who needs a non-event documents which harness writes the
  field and on which message, beside the rule.
- A pack's own fixture tests remain the guard for the harness actually
  writing the field; the oracle only checks that the field is or is not
  present in the corpus it is given.

## Not decided here

Whether to add a loader or oracle check that refuses a `structured_field`
instrument found only at unit level. That would turn the placement rule
above from documentation into an enforced check.
