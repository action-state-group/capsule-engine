# EU AI Act deterministic obligations pack

`EvidenceCompiler.compile_obligations` compiles `obligations-register.yaml`
(36 rows) into 26 obligation-profile Evidence Contracts and excludes 10 rows.
The compile makes no model call and uses no judgment: the output depends only
on the register rows and the evidence-class table in
`capsule_engine/register/compiler.py`. The pinned output is
`tests/fixtures/register-vectors/eu-ai-act-obligations/expected.json` (JCS
bytes).

A row is compiled when its evidence class is checked mechanically from sealed
records (FACT, RULE, CONFIRM, DOC) and its clause is not contested. A
compiled row that names the field it needs in `evidence_instrument` compiles
as `declared_not_measured` until a corpus carries that field; the five rows
shared with `register.yaml` name none and compile as `measured`. DOC rows keep the
presence-by-digest-only disclaimer and are never graded compliant.

## Compiled (26)

| Row | Article | Class |
|---|---|---|
| EU-05d | 5(1)(ba)/(bb) | RULE |
| EU-04 | 4 | DOC |
| EU-53 | 53(1)(b) | FACT |
| EU-55 | 55(1)(c) | FACT |
| EU-50-1 | 50(1) | FACT |
| EU-50-2 | 50(2) | FACT |
| EU-50-3 | 50(3) | FACT |
| EU-50-4 | 50(4) | FACT |
| EU-75 | 75 | CONFIRM |
| EU-12-1 | 12(1) | FACT |
| EU-12-2 | 12(2)(a)-(c) | DOC |
| EU-12-3 | 12(3) | FACT |
| EU-13 | 13(3)(f) | DOC |
| EU-14-3 | 14(3) | RULE |
| EU-14-4d | 14(4)(d) | FACT |
| EU-14-4e | 14(4)(e) | FACT |
| EU-15 | 15(1), 15(4) | FACT |
| EU-17 | 17(1)(i), 17(1)(j) | DOC |
| EU-26-2 | 26(2) | DOC |
| EU-26-5 | 26(5) | FACT |
| EU-26-6 | 26(6) | FACT |
| EU-26-7 | 26(7) | FACT |
| EU-26-11 | 26(11) | FACT |
| EU-27 | 27 | DOC |
| EU-09 | 9(2)(c) | DOC |
| EU-11 | 11 / Annex IV | DOC |

## Excluded (10)

| Row | Article | Class | Reason | Why |
|---|---|---|---|---|
| EU-05a | 5(1)(a) | JUDGED | `judgment_required` | Manipulation or deception is read from conversation text. |
| EU-05b | 5(1)(b) | JUDGED | `judgment_required` | Exploiting a vulnerability is read from conversation text. |
| EU-05c | 5(1)(c) | JUDGED | `judgment_required` | The input-class rule cannot yet tell a social score apart, so it falls back to a judged reading. |
| EU-49 | 49(1)-(3) | CONFIRM | `contested_clause` | Applies on its literal text but presupposes the 2 Dec 2027 high-risk regime. |
| EU-72 | 72(1)-(2) | FACT | `contested_clause` | Same as EU-49. |
| EU-73 | 73(2)-(4) | FACT | `contested_clause` | Same as EU-49. |
| EU-86 | 86(1) | JUDGED | `judgment_required` | Whether an explanation is clear and meaningful is a judged reading; the delivery pairing alone does not establish the obligation. |
| EU-14-4b | 14(4)(b) | JUDGED | `judgment_required` | Approval latency is an indicator only; "not a rubber stamp" is a judged reading. |
| EU-26-1 | 26(1) | STATE | `system_of_record_read_required` | Needs the current allowed-actions list compiled from the provider's instructions for use, which a sealed record does not carry. |
| EU-26-4 | 26(4) | JUDGED | `judgment_required` | "Relevant and sufficiently representative" is only ever a judged reading against a declared input policy. |

## Not in the register

The Annex I row (agents embedded in Annex I products) adds no evidence of its
own. It changes when the Article 6-27 rows apply, so there is nothing to
compile for it.
