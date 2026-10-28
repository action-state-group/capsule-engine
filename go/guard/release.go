package guard

import (
	"fmt"
	"slices"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// releaseConfig is seller.release_on_acceptance/1.0.0's config: the closed
// set of classes a disclosure may declare, the classes whose accepted record
// seals an acceptance, and the disclosures the rule applies to.
//
// ExceptRecipientRoles (seller.release_on_acceptance/1.1.0: platform) are the
// recipient roles a disclosure to which the rule does not apply: it reaches
// no buyer, so there is no acceptance to cite, and recipient_role measures
// what it may carry.
//
// Binding (seller.release_on_acceptance/1.2.0: same_deal_same_buyer) binds
// the acceptance from the history rather than a citation
// (releaseFromHistory), and PreAcceptanceClasses are the classes that go to
// any buyer before acceptance. 1.0.0 and 1.1.0 set neither.
type releaseConfig struct {
	ReleaseClasses       []string `yaml:"release_classes"`
	AcceptanceClasses    []string `yaml:"acceptance_classes"`
	ActionClasses        []string `yaml:"action_classes"`
	ExceptRecipientRoles []string `yaml:"except_recipient_roles"`
	Binding              string   `yaml:"binding"`
	PreAcceptanceClasses []string `yaml:"pre_acceptance_classes"`
}

// sameDealSameBuyer is the binding that reads the sale's acceptance from the
// history (release_on_acceptance.py SAME_DEAL_SAME_BUYER).
const sameDealSameBuyer = "same_deal_same_buyer"

// checkReleaseBinding refuses a binding this port does not know, as the
// engine refuses one when the check runs ("release_on_acceptance has no
// binding"): none (cited) and same_deal_same_buyer are the two.
func checkReleaseBinding(c releaseConfig, id string) error {
	if c.Binding != "" && c.Binding != sameDealSameBuyer {
		return fmt.Errorf("%s: release_on_acceptance has no binding %q", id, c.Binding)
	}
	return nil
}

// releaseNotBound is release_on_acceptance.py _NOT_BOUND: every failure on
// the citation reads the same, so a buyer cannot learn whether another
// buyer's acceptance exists.
const releaseNotBound = "the disclosure does not cite this sale's sealed acceptance by this recipient"

// NoCapsuleVerifier is why a disclosure that meets every other condition of
// release_on_acceptance is not evaluated: the engine passes it only once the
// cited acceptance re-verifies in the ledger (agent_action_capsule.verify
// and the key-revocation timeline), and this plugin has no capsule
// verifier. Never a pass.
const NoCapsuleVerifier = "the cited acceptance could not be re-verified: this plugin has no capsule verifier"

// checkReleaseOnAcceptance is capsule-engine
// guards/checks/release_on_acceptance.py at 21975ce (and 7339028, where
// seller.release_on_acceptance/1.1.0 is out of scope for a disclosure whose
// recipient_role is in except_recipient_roles): personal data is
// released only to the buyer who accepted. A disclosure in action_classes
// passes only when its representation_class is in release_classes, it names
// task_authority_ref, item_ref and target, it cites (cited_mandate_capsule_id)
// the sale's acceptance (saleAcceptance, as single_commitment reads it:
// keyed on item_ref, and an acceptance a live history leaves unknown is
// none; capsule-engine #137),
// that acceptance was sealed for the same target, and the cited record
// re-verifies in the ledger. Every other such disclosure fails, and so does
// an action whose class is missing or has no taxonomy row (resolved there,
// so a legacy alias counts as its canonical class); another known class is
// out of scope. A failure is an integrity failure (integrityChecks): refused
// whatever the pack declares, never cleared by an approval.
//
// The one place this port departs from the engine: the last condition needs
// the capsule verifier this plugin does not have, so a disclosure meeting
// every other one is n/a, not evaluated (NoCapsuleVerifier), which the
// report reads as not_evaluable: never a pass, never an allow.
//
// The evidence names only the declared class, whether the release was
// bound, and a missing field's name: never the disclosed data, a reference,
// a counterparty or a capsule id.
func checkReleaseOnAcceptance(a Action, h *historyReader, in checkInputs, c releaseConfig, tax taxonomy) (out Constraint) {
	const id = "release_on_acceptance"
	method := "cited_sealed_acceptance_v1"
	if c.Binding != "" {
		method = releaseHistoryMethod
	}
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	evidence := func(class *string, bound bool, missing string) canon.Value {
		cls, field := canon.Null(), canon.Null()
		if class != nil {
			cls = canon.Str(*class)
		}
		if missing != "" {
			field = canon.Str(missing)
		}
		return canon.Obj(canon.M("constraint_id", canon.Str(id)), canon.M("representation_class", cls),
			canon.M("bound_to_acceptance", canon.Bool(bound)), canon.M("missing_field", field))
	}
	fail := func(class *string, missing, note string) Constraint {
		out := decided(id, method, Fail, evidence(class, false, missing))
		out.failNote = note
		return out
	}
	row, known := actionClass{}, false
	if a.ActionClass != nil {
		row, known = tax.resolve(*a.ActionClass)
	}
	if !known {
		const note = "the action class has no taxonomy row; it cannot be shown to be outside the rule"
		if c.Binding != "" {
			return releaseFail(a.DisclosedClasses, "action_class", note)
		}
		return fail(nil, "action_class", note)
	}
	if !slices.Contains(c.ActionClasses, row.Name) {
		return outOfScope(id, method)
	}
	if c.ExceptRecipientRoles != nil {
		rd = append(rd, "recipient_role")
		if a.RecipientRole != nil && slices.Contains(c.ExceptRecipientRoles, *a.RecipientRole) {
			out := outOfScope(id, method)
			out.scopeNote = "the recipient is a " + *a.RecipientRole + ", not a buyer; recipient_role measures it"
			return out
		}
	}
	if c.Binding != "" {
		return releaseFromHistory(a, h, in, c, &rd)
	}
	rd = append(rd, "representation_class")
	cls := a.RepresentationClass
	if cls == nil || !slices.Contains(c.ReleaseClasses, *cls) {
		// An undeclared class's name is the producer's free text, never recorded.
		return fail(nil, "representation_class", "the disclosure declares no class this rule knows; it is not released")
	}
	for _, f := range []struct {
		name  string
		value *string
	}{{"task_authority_ref", a.TaskAuthorityRef}, {itemRefInput, a.ItemRef}, {"target", a.Target}} {
		rd = append(rd, f.name)
		if f.value != nil {
			continue
		}
		// An ignored input is named, never the value it was given.
		shape := "no " + f.name
		if slices.Contains(a.ignoredInputs, f.name) {
			shape = "the " + f.name + " input was not in its agreed shape"
		}
		return fail(cls, f.name, "the disclosure has "+shape+"; it cannot be bound to an acceptance")
	}
	rd = append(rd, "cited_mandate_capsule_id")
	if a.CitedMandateCapsuleID == nil {
		return fail(cls, "", "the disclosure cites no acceptance")
	}
	acceptance, _ := saleAcceptance(a, h, in, c.AcceptanceClasses)
	if acceptance == nil || *a.CitedMandateCapsuleID != acceptance.CapsuleID || acceptance.Target == nil || *a.Target != *acceptance.Target {
		return fail(cls, "", releaseNotBound)
	}
	return notEvaluable(id, method, NoCapsuleVerifier)
}

// releaseHistoryMethod is 1.2.0's method (release_on_acceptance.py
// _HISTORY_METHOD).
const releaseHistoryMethod = "same_deal_same_buyer_v1"

// releaseNotAccepted is 1.2.0's one reason for no acceptance, an unknown
// one, and one in another deal or by another buyer (_NOT_ACCEPTED), so a
// buyer cannot learn whether someone else accepted.
const releaseNotAccepted = "the history given shows no acceptance of this sale by this buyer in this deal"

// perPersonKinds are the identity kinds that name one person (capsule-cli
// deal_counterparty.go perPartyKinds); noPerPersonKinds name the thread's
// own buyer when neither side seals one. A payee is never compared.
var (
	perPersonKinds   = []string{"email", "phone", "profile_id", "relay_address"}
	noPerPersonKinds = []string{"name", "domain"}
)

// releaseEvidence is 1.2.0's evidence (ClassesEvidence): the class names
// the disclosure gives (null when it gives none), whether it was bound to an
// acceptance, and a missing field's name.
func releaseEvidence(classes []string, bound bool, missing string) canon.Value {
	cls, field := canon.Null(), canon.Null()
	if len(classes) > 0 {
		cls = canon.Strs(classes)
	}
	if missing != "" {
		field = canon.Str(missing)
	}
	return canon.Obj(canon.M("constraint_id", canon.Str("release_on_acceptance")), canon.M("disclosed_classes", cls),
		canon.M("bound_to_acceptance", canon.Bool(bound)), canon.M("missing_field", field))
}

func releaseFail(classes []string, missing, note string) Constraint {
	out := decided("release_on_acceptance", releaseHistoryMethod, Fail, releaseEvidence(classes, false, missing))
	out.failNote = note
	return out
}

// releaseFromHistory is release_on_acceptance.py _release_from_history at
// capsule-engine 9066490 (seller.release_on_acceptance/1.2.0, binding
// same_deal_same_buyer), for a disclosure in scope. It reads the classes the
// share gives (disclosed_classes), never representation_class: none, or one
// outside release_classes, fails with its own reason; all in
// pre_acceptance_classes (the listing's public pickup area) passes to any
// buyer. Anything else passes only when the share names its item_ref, its
// deal_id and a whole counterparty block (wellFormedCounterparty; a block
// with a member of another kind or shape is unusable, never read as one
// with that member absent), and the sale's acceptance (saleAcceptance, the
// record single_commitment reads; an unknown one is none) is in the same
// deal and was sealed for the same buyer (sameBuyer, against the
// counterparty its check sealed, which the history carries beside the act).
// The acceptance is not cited and not re-verified: the history is the
// producer's own sealed records, the trust single_commitment places in them.
func releaseFromHistory(a Action, h *historyReader, in checkInputs, c releaseConfig, rd *[]string) Constraint {
	const id = "release_on_acceptance"
	*rd = append(*rd, "disclosed_classes")
	classes := a.DisclosedClasses
	if classes == nil {
		return releaseFail(nil, "disclosed_classes", "the share names no class of data")
	}
	if slices.ContainsFunc(classes, func(cl string) bool { return !slices.Contains(c.ReleaseClasses, cl) }) {
		return releaseFail(classes, "", "a credential, a verification code, a payment card or an ID document is never shared with anyone")
	}
	if !slices.ContainsFunc(classes, func(cl string) bool { return !slices.Contains(c.PreAcceptanceClasses, cl) }) {
		return decided(id, releaseHistoryMethod, Pass, releaseEvidence(classes, false, ""))
	}
	for _, f := range []struct {
		name    string
		present bool
	}{{itemRefInput, a.ItemRef != nil}, {"deal_id", a.DealID != nil},
		{"counterparty", len(a.counterpartyIDs) > 0 && a.counterpartyFPAlg != ""}} {
		*rd = append(*rd, f.name)
		if f.present {
			continue
		}
		// An ignored input is named, never the value it was given.
		shape := "no " + f.name
		if slices.Contains(a.ignoredInputs, f.name) {
			shape = "the " + f.name + " input was not in its agreed shape"
		}
		return releaseFail(classes, f.name, "the disclosure has "+shape+"; it cannot be bound to an acceptance")
	}
	if a.counterpartyMalformed {
		return releaseFail(classes, "counterparty", "the counterparty input was not in its agreed shape; it cannot be bound to an acceptance")
	}
	acceptance, unknown := saleAcceptance(a, h, in, c.AcceptanceClasses)
	if unknown != "" || acceptance == nil || acceptance.DealID == nil || *acceptance.DealID != *a.DealID ||
		!sameBuyer(acceptance.threadCounterparty, a.counterpartyFPAlg, a.counterpartyIDs) {
		return releaseFail(classes, "", releaseNotAccepted)
	}
	return decided(id, releaseHistoryMethod, Pass, releaseEvidence(classes, true, ""))
}

// sameBuyer is release_on_acceptance.py same_buyer: whether the counterparty
// an acceptance's check sealed and the one a disclosure's check sealed are
// one buyer: under the same algorithm, and either at least one per-person
// kind on both sides with every per-person kind on both sides equal, or,
// when neither side seals a per-person kind, a name and a domain on both
// sides, each equal.
func sameBuyer(acceptance *threadCounterparty, fpAlg string, ids map[string]string) bool {
	if acceptance == nil || fpAlg == "" || len(ids) == 0 || acceptance.FPAlg != fpAlg {
		return false
	}
	theirs := acceptance.IDs
	has := func(m map[string]string, k string) bool { _, ok := m[k]; return ok }
	if !slices.ContainsFunc(perPersonKinds, func(k string) bool { return has(theirs, k) || has(ids, k) }) {
		return !slices.ContainsFunc(noPerPersonKinds, func(k string) bool { return !has(theirs, k) || !has(ids, k) || theirs[k] != ids[k] })
	}
	shared := 0
	for _, k := range perPersonKinds {
		if has(theirs, k) && has(ids, k) {
			if theirs[k] != ids[k] {
				return false
			}
			shared++
		}
	}
	return shared > 0
}
