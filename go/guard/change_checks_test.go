package guard

import (
	"bytes"
	"encoding/json"
	"slices"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// changeChecks are the checks change-checks-parity.json gates: each is run
// by this port, never left not yet run, and matches Python's result and
// evidence digest on every scenario.
var changeChecks = []string{"channel_change", "material_fields_changed", "offer_fields_changed"}

// THE GATE FOR THE CHANGE CHECKS: decision by decision against a live Python
// run of capsule-engine's own everyday pack at the commit it names
// (testdata/parity/generate_change_checks.py), compared in full (outcome,
// every constraint's result and evidence digest). Each step enters the
// ledger with Python's outcome, so both see the same history.
func TestChangeChecksMatchPythonDecisions(t *testing.T) {
	var p parityFile
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "change-checks-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	if p.CapsuleEngineCommit != "a710636c29814ce67e838c673026fd2063d913f8" {
		t.Fatalf("change-check parity run at %s", p.CapsuleEngineCommit)
	}
	e, _ := everydayEngine(t)
	var ledger []Record
	decided := map[string]int{}
	for _, s := range p.Scenarios {
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.Check(a, ledger)
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		for _, check := range changeChecks {
			c, ok := d.Constraint(check)
			if !ok || notYetRun(c) {
				t.Errorf("%s: %s is not run by this port", s.Scenario, check)
			}
			if c.Result != NotApplicable {
				decided[check]++
			}
		}
		compareToPython(t, s.Scenario, e, a, d, s)
		ledger = append(ledger, RecordFor(a, Decision{Outcome: s.Outcome}))
	}
	// The corpus decides each check both ways, not only n/a.
	for _, check := range changeChecks {
		if decided[check] < 2 {
			t.Errorf("%s: decided on %d scenario(s)", check, decided[check])
		}
	}
}

// jsonMembers is a JSON object member by member, each value kept as its
// raw JSON (of any type, so a test can seal a wrong-shaped one).
type jsonMembers map[string]json.RawMessage

// members decodes raw as a JSON object, failing the test otherwise.
func members(t *testing.T, what string, raw json.RawMessage) jsonMembers {
	t.Helper()
	var m jsonMembers
	if err := json.Unmarshal(raw, &m); err != nil || m == nil {
		t.Fatalf("%s is not a JSON object: %v", what, err)
	}
	return m
}

// encode is m as JSON, failing the test on an error.
func (m jsonMembers) encode(t *testing.T) json.RawMessage {
	t.Helper()
	raw, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

// sealedRecord is testdata/records/pay-check-record.json with its deal body
// changed by edit and the header's agent_input_digest resealed over it.
func sealedRecord(t *testing.T, edit func(body jsonMembers)) []byte {
	t.Helper()
	rec := members(t, "the record", readFile(t, "testdata", "records", "pay-check-record.json"))
	input := members(t, "agent_input", rec["agent_input"])
	body := members(t, "agent_input.body", input["body"])
	edit(body)
	input["body"] = body.encode(t)
	rec["agent_input"] = input.encode(t)
	v, err := canon.FromJSON(rec["agent_input"])
	if err != nil {
		t.Fatal(err)
	}
	digest, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	attestation := members(t, "model_attestation", rec["model_attestation"])
	compute := members(t, "model_attestation.compute_attestation", attestation["compute_attestation"])
	if compute["agent_input_digest"], err = json.Marshal(digest); err != nil {
		t.Fatal(err)
	}
	attestation["compute_attestation"] = compute.encode(t)
	rec["model_attestation"] = attestation.encode(t)
	return rec.encode(t)
}

// j is a JSON literal as a raw value.
func j(s string) json.RawMessage { return json.RawMessage(s) }

// A deal record's channel and first_contact_channel are read as the
// engine's deal reader reads them (report/replay.py _bridge_deal_check
// _text at 365eb92): a non-empty string is taken, any other value is
// dropped, never refused, so the check sees no channel.
func TestChannelChangeReadsADealRecordAsTheEngineDoes(t *testing.T) {
	e, _ := everydayEngine(t)
	for _, c := range []struct {
		name           string
		channel, first json.RawMessage
		want           Result
		missing        string
	}{
		{"same", j(`"marketplace"`), j(`"marketplace"`), Pass, ""},
		{"moved", j(`"whatsapp"`), j(`"marketplace"`), Fail, ""},
		{"channel a number", j(`3`), j(`"marketplace"`), NotApplicable, "channel"},
		{"channel empty", j(`""`), j(`"marketplace"`), NotApplicable, "channel"},
		{"first contact an object", j(`"web"`), j(`{"kind":"web"}`), NotApplicable, "first_contact_channel"},
		{"neither", nil, nil, NotApplicable, "channel"},
	} {
		raw := sealedRecord(t, func(body jsonMembers) {
			if c.channel != nil {
				body["channel"] = c.channel
			}
			if c.first != nil {
				body["first_contact_channel"] = c.first
			}
		})
		a, err := DecodeRecord(raw, "")
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		got, _ := d.Constraint("channel_change")
		if got.Result != c.want || got.MissingField() != c.missing {
			t.Errorf("%s: %s missing %q, want %s missing %q", c.name, got.Result, got.MissingField(), c.want, c.missing)
		}
	}
}

// Each list's basis, as the engine's wicket pins the list, is the one
// capsule-cli seals (internal/cli/deal_rule_inputs_test.go) over the list in
// the agreed field-name list, in its order.
func TestFieldListBasesAreTheProducers(t *testing.T) {
	e, _ := everydayEngine(t)
	for _, c := range []struct {
		name     string
		got      fieldCount
		agreed   []string
		producer string
	}{
		{"material", e.material, []string{"terms.item", "terms.quantity", "terms.price_minor", "terms.deposit_minor", "terms.currency",
			"terms.when", "terms.place", "terms.conditions", "recourse.rail", "recourse.refundable", "who.payee"},
			"effa16bb95192a3208f24daa127a4f0ac387095e139856b7ce390ebd96b772c3"},
		{"offer", e.offer, []string{"terms.item", "terms.quantity", "terms.price_minor", "terms.deposit_minor", "terms.conditions",
			"recourse.refundable"}, "52ddf74622010caba9ef3baf4d9600365e8670169fb4a56a3c5a5a1cea93739d"},
	} {
		if !slices.Equal(c.got.CountedFields, c.agreed) || c.got.basis != c.producer {
			t.Errorf("%s list %v basis %s", c.name, c.got.CountedFields, c.got.basis)
		}
	}
}

// A deal record's offer count and basis are read as the material ones are.
// A record whose user stated no offer key carries neither (AMENDMENT 1):
// n/a, in scope, naming offer_fields_changed; never a pass.
func TestOfferFieldsChangedReadsADealRecordAsTheEngineDoes(t *testing.T) {
	e, _ := everydayEngine(t)
	basis := j(`"52ddf74622010caba9ef3baf4d9600365e8670169fb4a56a3c5a5a1cea93739d"`)
	for _, c := range []struct {
		name         string
		count, basis json.RawMessage
		want         Result
		missing      string
	}{
		{"matches", j(`0`), basis, Pass, ""},
		{"differs", j(`1`), basis, Fail, ""},
		{"nothing stated", nil, nil, NotApplicable, "offer_fields_changed"},
		{"count a string", j(`"1"`), basis, NotApplicable, "offer_fields_changed"},
		{"material basis", j(`0`), j(`"effa16bb95192a3208f24daa127a4f0ac387095e139856b7ce390ebd96b772c3"`), NotApplicable, "offer_fields_basis"},
	} {
		raw := sealedRecord(t, func(body jsonMembers) {
			if c.count != nil {
				body["offer_fields_changed"] = c.count
			}
			if c.basis != nil {
				body["offer_fields_basis"] = c.basis
			}
		})
		a, err := DecodeRecord(raw, "")
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		got, _ := d.Constraint("offer_fields_changed")
		if got.Result != c.want || got.MissingField() != c.missing {
			t.Errorf("%s: %s missing %q, want %s missing %q", c.name, got.Result, got.MissingField(), c.want, c.missing)
		}
	}
}

// A deal record's material_fields_changed is taken only as an integer and
// material_fields_basis only as a non-empty string, as the engine's deal
// reader takes them (_minor and _text); any other value is dropped.
func TestMaterialFieldsChangedReadsADealRecordAsTheEngineDoes(t *testing.T) {
	e, _ := everydayEngine(t)
	basis := j(`"effa16bb95192a3208f24daa127a4f0ac387095e139856b7ce390ebd96b772c3"`)
	for _, c := range []struct {
		name         string
		count, basis json.RawMessage
		want         Result
		missing      string
	}{
		{"unchanged", j(`0`), basis, Pass, ""},
		{"two changed", j(`2`), basis, Fail, ""},
		{"count a string", j(`"1"`), basis, NotApplicable, "material_fields_changed"},
		{"count a boolean", j(`true`), basis, NotApplicable, "material_fields_changed"},
		{"basis empty", j(`1`), j(`""`), NotApplicable, "material_fields_basis"},
		{"basis a number", j(`1`), j(`7`), NotApplicable, "material_fields_basis"},
	} {
		raw := sealedRecord(t, func(body jsonMembers) {
			body["material_fields_changed"] = c.count
			body["material_fields_basis"] = c.basis
		})
		a, err := DecodeRecord(raw, "")
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		got, _ := d.Constraint("material_fields_changed")
		if got.Result != c.want || got.MissingField() != c.missing {
			t.Errorf("%s: %s missing %q, want %s missing %q", c.name, got.Result, got.MissingField(), c.want, c.missing)
		}
	}
}
