# Go vectors for the live-check history ledger (capsule-engine branch live-check-history-ledger)

Source of truth: capsule-engine tests/fixtures/live-history/ (README.md there is copied here).
`expected_live.json` = every check input in check-inputs/ decided live under asg/seller/0.1.2, three ways:
  history "as-sealed" (capsulectl main 65f54e528a788cace28b8e2df92f652b5d5629a9, every act fyi, no disposition),
  "disposition-accept" (test adds {"decision":"accept"} as `disposition` on every history capsule; synthetic),
  "absent" (history + history_scope removed),
  "disposition-accept-commit-names-no-item" (disposition-accept, item_ref removed from every agreement.accept history entry),
  "disposition-accept-record-names-no-item" (disposition-accept, top-level item_ref removed from the envelope).
Per case: `ledger` = the records the history builder wrote, in ledger order (capsule_id, live_history state,
decision, action_class, deal_id, item_ref, task_authority_ref), `complete`, `outcome`, `verdict`,
`single_commitment` {result, reason, evidence}, and every seller rule's result.

## The rule the mirror implements (capsule_engine/report/live_history.py + guards/checks/single_commitment.py)
1. Order: stable sort of `history` ascending by capsule `timestamp` (RFC 3339 with zone); ties keep envelope order.
2. Each entry -> one record. Act = bound `action-record/v0` (agent_input digest == model_attestation.compute_attestation.agent_input_digest)
   with body.action_class + taxonomy_version. task authority = ref.digest of the ONE authority_basis entry of type
   task_authority; item_ref = entry.item_ref (64 lowercase hex); deal = agent_input.chain_id.
   State: `disposition` iff the capsule has disposition.decision (non-empty string), else `no_disposition`.
   NEVER infer a decision from authority_basis. Not an act -> `unread` (keeps item_ref if well-shaped).
3. `incomplete` (one record after the acts) when: history missing / not a list, history_scope.complete is not
   JSON true, an entry has no parseable timestamp, or an entry has an item_ref member not 64 lowercase hex.
4. s11 single_commitment (commit classes only, needs task_authority_ref + item_ref): sale = item_ref ALONE
   (capsulectl seals one task authority per thread, salted - A and B cite different refs).
   Scan operator's records in order: incomplete anywhere -> n/a missing_field "history". Before the first
   acceptance, for records with the same item_ref: unread -> n/a "history"; acceptance class (agreement.accept)
   with no_disposition -> n/a "disposition". Acceptance = acceptance class, decision accept, not dry run.
   Follows the acceptance (pass) iff (cites it AND same target) OR same deal_id (both set). Else fail.
   GUARD: item_ref ABSENT ->
     - on the checked record (missing or not 64 lowercase hex): n/a missing_field "item_ref", FAILS CLOSED (was n/a, allowed);
     - in history: a `disposition` record whose decision is accept, class in acceptance_classes, not dry run, with NO item_ref,
       seen before any acceptance of this sale -> n/a missing_field "item_ref". A no_disposition / unread / non-acceptance
       record without item_ref is NOT held (it is not an accepted commitment).
   Every n/a from 4 (history | disposition | item_ref) is fails_closed: the decision is DENY whatever else allows/asks;
   verdict not_evaluable if nothing failed. Check order per record: incomplete -> accepted-commit-without-item ->
   item_ref mismatch skip -> unread -> class -> no_disposition -> accepted.
   NOTE: a replay never has item_ref, so every replayed seller commit-class check is now DENY (not_evaluable) too.
5. dedupe skips every history record whose state is not `disposition` (fyi history dedupes as an empty ledger).
   caps / counterparty_seen_before / counterparty_identity_change / required_disclosure already filter on
   disposition.decision accept, so fyi history is invisible to them; a `disposition` history act IS read by them.
