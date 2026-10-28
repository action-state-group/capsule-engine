package guard

import (
	"encoding/json"
	"slices"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// singleCommitmentConfig is seller.single_commitment/1.0.0's config: the
// classes that are an acceptance and the classes that are a commitment.
type singleCommitmentConfig struct {
	AcceptanceClasses []string `yaml:"acceptance_classes"`
	CommitClasses     []string `yaml:"commit_classes"`
}

// itemRefInput names the checker input's top-level item_ref.
const itemRefInput = "item_ref"

// checkSingleCommitment is capsule-engine guards/checks/single_commitment.py
// at 0ab20965 (#137, with its guard): one sale, one accepted
// commitment. The sale is the action's item_ref alone: capsulectl seals a
// task authority per thread, salted, so two threads of one sale never share
// one. Its acceptance is saleAcceptance's. Once there is one, a commitment
// fails unless it follows it: it cites the acceptance
// (cited_mandate_capsule_id) AND is addressed to the counterparty that
// accepted (target), or it is in the acceptance's own deal (deal_id equal,
// both set; live, an act record seals no counterparty and a typed check
// cites nothing). A commitment missing task_authority_ref is an in-scope
// n/a naming it. One missing item_ref, or whose sale's acceptance the live
// history leaves unknown, is an in-scope n/a naming the missing input
// (item_ref, history or disposition) that fails closed: refused, whatever
// else would allow or ask (Decision.notEvaluated). A failure is an integrity
// failure (integrityChecks): refused, never asked.
//
// Nothing it records names a reference: the evidence says only whether the
// sale has an acceptance, and the reasons name no value, so no finding can
// link one buyer's thread to another's.
func checkSingleCommitment(a Action, h *historyReader, in checkInputs, c singleCommitmentConfig) (out Constraint) {
	const id, method = "single_commitment", "first_sealed_acceptance_v1"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.CommitClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "task_authority_ref")
	if a.TaskAuthorityRef == nil {
		out = missingField(id, method, "task_authority_ref")
		out.pending = "the action cites no task authority; the sale could not be identified"
		return out
	}
	rd = append(rd, itemRefInput)
	if a.ItemRef == nil {
		out = missingField(id, method, itemRefInput)
		out.pending = "the action names no item; the sale could not be identified"
		if slices.Contains(a.ignoredInputs, itemRefInput) {
			out.pending = "the item_ref input was not in its agreed shape; the sale could not be identified"
		}
		out.failsClosed = true
		return out
	}
	acceptance, unknown := saleAcceptance(a, h, in, c.AcceptanceClasses)
	if unknown != "" {
		out = missingField(id, method, unknown)
		out.pending = saleUnknownReasons[unknown]
		out.failsClosed = true
		return out
	}
	evidence := func(accepted bool) canon.Value {
		return canon.Obj(canon.M("constraint_id", canon.Str(id)), canon.M("sale_has_acceptance", canon.Bool(accepted)))
	}
	if acceptance == nil {
		return decided(id, method, Pass, evidence(false))
	}
	rd = append(rd, "cited_mandate_capsule_id", "target", "deal_id")
	cites := a.CitedMandateCapsuleID != nil && *a.CitedMandateCapsuleID == acceptance.CapsuleID
	sameCounterparty := a.Target != nil && acceptance.Target != nil && *a.Target == *acceptance.Target
	sameDeal := a.DealID != nil && acceptance.DealID != nil && *a.DealID == *acceptance.DealID
	if cites && sameCounterparty || sameDeal {
		return decided(id, method, Pass, evidence(true))
	}
	out = decided(id, method, Fail, evidence(true))
	out.failNote = "this sale already has an accepted commitment"
	return out
}

// saleUnknownReasons are single_commitment.py _UNKNOWN_REASONS: why the
// sale's acceptance is not known, by the missing input named.
var saleUnknownReasons = map[string]string{
	"history":     "the history may be missing an act of this sale; whether it has an acceptance is not known",
	"disposition": "an earlier commitment in this sale seals no disposition; whether it was accepted is not known",
	itemRefInput:  "an accepted commitment in the history names no item; whether it is this sale's is not known",
}

// saleAcceptance is single_commitment.py sale_acceptance (#137): the sale's
// acceptance, the first record in ledger order among a's operator's whose
// class is an acceptance class, decided accept and not a dry run, carrying
// a's item_ref; or, when the ledger is a check input's history, the input
// whose absence leaves it unknown (MIRROR.md's order, record by record,
// before an acceptance is found): the history may be missing acts
// (history: incomplete, at any point); an accepted commitment in the
// history names no item (item_ref); an act of this sale could not be read
// (history); an act of this sale of an acceptance class seals no
// disposition (disposition). A history is read oldest first, as the
// engine's history ledger writes it (liveOrder). Shared with
// release_on_acceptance, so both read one acceptance per sale, and an
// unknown one releases nothing.
func saleAcceptance(a Action, h *historyReader, in checkInputs, classes []string) (*Record, string) {
	if in.history && in.historyIncomplete {
		return nil, "history"
	}
	ledger := h.find(Query{Where: map[string]string{FieldOperator: a.Operator}})
	if in.history {
		ledger = liveOrder(ledger)
	}
	for i, r := range ledger {
		if r.Operator != a.Operator {
			continue
		}
		accepted := r.acceptedCommitment(classes)
		switch {
		case r.live == liveDisposition && r.ItemRef == nil && accepted:
			// An accepted commitment in the history that names no item may
			// be this sale's.
			return nil, itemRefInput
		case r.ItemRef == nil || *r.ItemRef != *a.ItemRef:
			continue
		case r.live == liveUnread:
			return nil, "history"
		case r.ActionClass == nil || !slices.Contains(classes, *r.ActionClass):
			continue
		case r.live == liveNoDisposition:
			return nil, "disposition"
		case accepted:
			return &ledger[i], ""
		}
	}
	return nil, ""
}

// acceptedCommitment is single_commitment.py _accepted_commitment: an
// acceptance class, decided accept (a history act's sealed disposition, as
// the engine's history ledger writes it), not a dry run.
func (r Record) acceptedCommitment(classes []string) bool {
	decision := r.Decision
	if r.live != "" {
		decision = r.sealed
	}
	return r.ActionClass != nil && slices.Contains(classes, *r.ActionClass) && decision != nil && *decision == "accept" &&
		(r.DryRun == nil || !*r.DryRun)
}

// liveOrder is a history oldest first by its sealed timestamp, entries of
// the same instant in the order given (live_history.py: capsulectl sends the
// history newest first; the sort is stable).
func liveOrder(ledger []Record) []Record {
	out := slices.Clone(ledger)
	slices.SortStableFunc(out, func(x, y Record) int {
		tx, _ := parseInstant(x.Timestamp)
		ty, _ := parseInstant(y.Timestamp)
		return tx.Compare(ty)
	})
	return out
}

// WithItemRef is a with the checker input's top-level item_ref, as
// capsule-engine's action_for_check_input (report/replay.py) sets it: when
// it is 64 lowercase hex, and otherwise named in the ignored inputs, never
// its value. Absent (nil or null), a is unchanged.
func WithItemRef(a Action, raw json.RawMessage) Action {
	if isJSONNull(raw) {
		return a
	}
	var item string
	if json.Unmarshal(raw, &item) == nil && hex64.MatchString(item) {
		a.ItemRef = &item
		return a
	}
	a.ignoredInputs = append(slices.Clone(a.ignoredInputs), itemRefInput)
	return a
}
