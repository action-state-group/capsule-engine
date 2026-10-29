package guard

import (
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

func TestRequiredFieldsReadsEveryNormalizedField(t *testing.T) {
	// packs/schema.py NORMALIZED_ACTION_FIELDS: each must name an Action field.
	for _, f := range []string{"amount_minor", "currency", "target", "cited_mandate_capsule_id", "equivalence_key",
		"model_id", "provider", "rail", "counterparty_account_ref", "outgoing_content", "recurrence", "recipient_role",
		"disclosed_classes", "refundable", "material_fields_changed", "material_fields_basis", "offer_fields_changed",
		"offer_fields_basis", "channel", "first_contact_channel", "upfront_amount_minor", "input_msat_per_million",
		"output_msat_per_million", "task_authority_ref", "representation_class", "statement_attributed_to",
		"authorized_by", "proposal_at", "handover", "no_proposal", "item_ref", "deal_id", "listing_classes"} {
		if _, ok := actionFieldIndex[f]; !ok {
			t.Errorf("Action carries no field %s", f)
		}
	}
}

func TestRequiredFieldsRefusesAFieldTheActionDoesNotCarry(t *testing.T) {
	_, err := requiredFieldsOf(pack.Pack{ActionSemantics: []pack.ActionSemantic{
		{ActionType: "x.do", ActionClass: "money.purchase", RequiredFields: []string{"no_such_field"}}}})
	if err == nil {
		t.Fatal("a required field Action does not carry was accepted")
	}
}

func TestRequiredFieldsTreatsEmptyAsMissing(t *testing.T) {
	empty, key := "", "k"
	required := map[string][]string{"inference.purchase": {"target", "equivalence_key", "disclosed_classes"}}
	a := Action{Verb: "inference.purchase", Target: &key, EquivalenceKey: &empty, DisclosedClasses: []string{"email"}}
	for _, tc := range []struct {
		name    string
		mutate  func(*Action)
		result  Result
		missing string
	}{
		{"empty string", func(*Action) {}, NotApplicable, "equivalence_key"},
		{"empty list", func(a *Action) { a.EquivalenceKey, a.DisclosedClasses = &key, []string{} }, NotApplicable, "disclosed_classes"},
		{"all set", func(a *Action) { a.DisclosedClasses = []string{"email"} }, Pass, ""},
	} {
		tc.mutate(&a)
		c, ok := checkRequiredFields(a, required)
		if !ok || c.Result != tc.result || c.missing != tc.missing || c.failsClosed != (tc.result == NotApplicable) {
			t.Errorf("%s: got ok=%v result=%v missing=%q failsClosed=%v", tc.name, ok, c.Result, c.missing, c.failsClosed)
		}
	}
	if _, ok := checkRequiredFields(Action{Verb: "buy"}, required); ok {
		t.Error("a verb naming no action type got a constraint")
	}
}
