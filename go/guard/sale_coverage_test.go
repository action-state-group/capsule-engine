package guard

import (
	"bytes"
	"encoding/json"
	"maps"
	"slices"
	"testing"
)

// SALE COVERAGE (capsule-engine #119, asg/everyday/0.3.4): channel_change,
// material_fields_changed, offer_fields_changed, refundability,
// task_authority and upfront_amount move to 1.1.0, each 1.0.0's config with
// marketplace.sale added to action_classes, so the same Go checks read a
// sale. marketplace.sale names approver_role account_holder (taxonomy 6) and
// every rule bound to the six declares ASK, so a sale failing any of them
// escalates. caps/5.0.0 is unchanged and does not list marketplace.sale:
// caps is a spending cap and a sale is money coming in, so sales are not
// spending-capped and a sale over the limit reads caps n/a, by design.
//
// Held against the engine side's corpus run through the engine at the pack's commit
// (generate_sale_coverage.py): per case the outcome, the disposition
// decision and verdict class, the failing constraints, the result of each of
// the six and of caps. The vectors also record the same corpus under 0.3.2,
// where every case allows with the six n/a; this build refuses 0.3.2 by its
// retired pin, so only the catalog half (0.3.6 from a710636c) runs here.
func TestSaleCoverageMatchesTheEngine(t *testing.T) {
	var corpus struct {
		TaskAuthorityRecord json.RawMessage `json:"task_authority_record"`
		Cases               []struct {
			Name   string          `json:"name"`
			DryRun bool            `json:"dry_run"`
			Action json.RawMessage `json:"action"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "sale-coverage-corpus.json"), &corpus); err != nil {
		t.Fatal(err)
	}
	type packInfo struct {
		PackID           string            `json:"pack_id"`
		DefinitionDigest string            `json:"definition_digest"`
		WicketDigests    map[string]string `json:"wicket_digests"`
		TaxonomyPin      *struct {
			Version string `json:"taxonomy_version"`
			Digest  string `json:"digest"`
		} `json:"taxonomy_pin"`
	}
	var py struct {
		CapsuleEngineCommit string              `json:"capsule_engine_commit"`
		Packs               map[string]packInfo `json:"packs"`
		Decisions           map[string]map[string]struct {
			Outcome            string            `json:"outcome"`
			Decision           string            `json:"decision"`
			VerdictClass       *string           `json:"verdict_class"`
			FailingConstraints []string          `json:"failing_constraints"`
			ResultsOfSix       map[string]string `json:"results_of_six"`
			CapsResult         string            `json:"caps_result"`
		} `json:"decisions"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "sale-coverage-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	e, vectorPack := everyday037Engine(t)
	info := py.Packs["0.3.6"]
	if py.CapsuleEngineCommit != parity(t, fixtures037).CapsuleEngineCommit || info.PackID != vectorPack.ID || info.DefinitionDigest != vectorPack.DefinitionDigest {
		t.Fatalf("vectors at %s for %s %s; Go runs %s %s", py.CapsuleEngineCommit, info.PackID, info.DefinitionDigest,
			vectorPack.ID, vectorPack.DefinitionDigest)
	}
	cited := map[string]string{}
	for _, w := range vectorPack.Wickets {
		cited[w.ID] = w.Digest
	}
	if !maps.Equal(cited, info.WicketDigests) {
		t.Fatalf("0.3.7 cites %v, the engine's %v", cited, info.WicketDigests)
	}
	if pin := vectorPack.Taxonomy; pin == nil || info.TaxonomyPin == nil || pin.Version != info.TaxonomyPin.Version || pin.Digest != info.TaxonomyPin.Digest {
		t.Fatalf("taxonomy pin %+v, the engine's %+v", vectorPack.Taxonomy, info.TaxonomyPin)
	}
	if retired := py.Packs["0.3.2"]; retired.PackID != "asg/everyday/0.3.2" || retired.DefinitionDigest != "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2" {
		t.Fatalf("the 0.3.2 half ran under %s %s", retired.PackID, retired.DefinitionDigest)
	}

	verdictClass := map[string]string{Deny: "blocked", Escalate: "hitl_dispatched"}
	decisions := py.Decisions["0.3.6"]
	if len(corpus.Cases) != 12 || len(decisions) != 12 {
		t.Fatalf("%d cases, %d decisions", len(corpus.Cases), len(decisions))
	}
	outcomes := map[string]string{}
	failing := map[string][]string{}
	for _, c := range corpus.Cases {
		want, ok := decisions[c.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", c.Name)
		}
		a, err := DecodeAction(bytes.NewReader(c.Action))
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		d, err := e.CheckWithTaskAuthority(a, nil, corpus.TaskAuthorityRecord)
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		var failingConstraints []string
		results := map[string]string{}
		for _, con := range d.Constraints {
			if con.Result == Fail {
				failingConstraints = append(failingConstraints, con.ID)
			}
			results[con.ID] = string(con.Result)
		}
		slices.Sort(failingConstraints)
		six := map[string]string{}
		for check := range want.ResultsOfSix {
			six[check] = results[check]
		}
		wantClass := ""
		if want.VerdictClass != nil {
			wantClass = *want.VerdictClass
		}
		decision := *RecordFor(a, d).Decision
		if d.Outcome != want.Outcome || decision != want.Decision || verdictClass[d.Outcome] != wantClass ||
			!slices.Equal(failingConstraints, want.FailingConstraints) || !maps.Equal(six, want.ResultsOfSix) || results["caps"] != want.CapsResult {
			t.Errorf("%s: Go %s (%s, %q), failing %v, six %v, caps %s; engine %s (%s, %q), failing %v, six %v, caps %s", c.Name,
				d.Outcome, decision, verdictClass[d.Outcome], failingConstraints, six, results["caps"],
				want.Outcome, want.Decision, wantClass, want.FailingConstraints, want.ResultsOfSix, want.CapsResult)
		}
		outcomes[c.Name], failing[c.Name] = d.Outcome, failingConstraints
	}
	// The cases the corpus exists for, named so a regenerated file that lost
	// one fails here.
	for c, want := range map[string][]string{
		"sale-material-terms-changed-asks": {"material_fields_changed"},
		"sale-offer-differs-asks":          {"offer_fields_changed"},
		"sale-non-refundable-asks":         {"refundability"},
		"sale-channel-moved-asks":          {"channel_change"},
		"sale-deposit-over-share-asks":     {"upfront_amount"},
		"sale-deposit-over-absolute-asks":  {"upfront_amount"},
		"sale-outside-task-asks":           {"task_authority"},
		"sale-two-limits-asks-citing-both": {"channel_change", "material_fields_changed"},
	} {
		if outcomes[c] != Escalate || !slices.Equal(failing[c], want) {
			t.Errorf("%s: %s failing %v, want escalate failing %v", c, outcomes[c], failing[c], want)
		}
	}
	for _, c := range []string{"sale-in-bounds-allows", "sale-material-basis-other-list-allows", "sale-no-refundable-field-allows", "sale-far-over-caps-allows"} {
		if outcomes[c] != Allow || len(failing[c]) != 0 {
			t.Errorf("%s: %s failing %v, want allow", c, outcomes[c], failing[c])
		}
	}
}
