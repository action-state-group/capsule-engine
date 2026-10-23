package guard

import (
	"bytes"
	"encoding/json"
	"slices"
	"testing"
)

// scalarParity is a <check>-parity.json file
// (testdata/parity/generate_scalar_ports.py): Python reference decisions
// from capsule-engine's own everyday pack at the pack's commit.
type scalarParity struct {
	CapsuleEngineCommit string           `json:"capsule_engine_commit"`
	Check               string           `json:"check"`
	Compare             []string         `json:"compare"`
	Scenarios           []parityScenario `json:"scenarios"`
	Records             []struct {
		Scenario            string             `json:"scenario"`
		Record              json.RawMessage    `json:"record"`
		Outcome             string             `json:"outcome"`
		Constraints         []parityConstraint `json:"constraints"`
		TaskAuthorityRecord json.RawMessage    `json:"task_authority_record"`
	} `json:"records"`
}

// checkScalarParity runs a ported check's differential: each normalized
// action through the shipped pack, the whole decision compared with
// Python's (the ledger growing as the engine's did), and each deal check
// record through DecodeRecord, the constraints the file names compared by
// result and evidence digest. The compared checks are never left not yet
// run. check names the file: a ported check, or "target" for the deal
// flow's target.
func checkScalarParity(t *testing.T, check string) {
	t.Helper()
	var p scalarParity
	if err := json.Unmarshal(readFile(t, "testdata", "parity", check+"-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	// recipient_role's run is at 7339028 (capsule-engine #173), whose
	// recipient_role/1.1.0 evidence the shipped pack's check writes; every
	// other check's definition is unchanged there.
	commit := "a710636c29814ce67e838c673026fd2063d913f8"
	if check == "recipient_role" {
		commit = "7339028ab2117d6c7adec89a26cd5022b8ac7140"
	}
	if p.CapsuleEngineCommit != commit || p.Check != check {
		t.Fatalf("%s parity run for %s at %s", check, p.Check, p.CapsuleEngineCommit)
	}
	if len(p.Scenarios) == 0 || len(p.Records) == 0 {
		t.Fatalf("%s parity: %d action and %d record scenarios", check, len(p.Scenarios), len(p.Records))
	}
	e, _ := everydayEngine(t)
	var ledger []Record
	for _, s := range p.Scenarios {
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.CheckWithTaskAuthority(a, ledger, s.TaskAuthorityRecord)
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		for _, id := range p.Compare {
			if c, ok := d.Constraint(id); !ok || notYetRun(c) {
				t.Errorf("%s: %s is not run (%+v)", s.Scenario, id, c)
			}
		}
		compareToPython(t, s.Scenario, e, a, d, s)
		ledger = append(ledger, RecordFor(a, Decision{Outcome: s.Outcome}))
	}
	for _, s := range p.Records {
		a, err := DecodeRecord(s.Record, "")
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.CheckWithTaskAuthority(a, nil, s.TaskAuthorityRecord)
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		compareVerdictToPython(t, s.Scenario, e, a, d, s.Outcome)
		for _, pc := range s.Constraints {
			if !slices.Contains(p.Compare, pc.ID) {
				continue
			}
			c, ok := d.Constraint(pc.ID)
			if !ok || notYetRun(c) {
				t.Errorf("%s: %s is not run", s.Scenario, pc.ID)
				continue
			}
			digest, _, err := c.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			if string(c.Result) != pc.Result || pc.EvidenceDigest == nil || digest != *pc.EvidenceDigest {
				t.Errorf("%s: %s Go %s %s, Python %s %v", s.Scenario, pc.ID, c.Result, digest, pc.Result, pc.EvidenceDigest)
			}
		}
	}
}

func TestRefundabilityParity(t *testing.T) { checkScalarParity(t, "refundability") }
func TestUpfrontAmountParity(t *testing.T) { checkScalarParity(t, "upfront_amount") }
func TestRecipientRoleParity(t *testing.T) { checkScalarParity(t, "recipient_role") }
func TestTaskAuthorityParity(t *testing.T) { checkScalarParity(t, "task_authority") }

// The deal flow's target (r02, r06): a deal check record's sealed payee
// fingerprint, as the engine's deal bridge maps it.
func TestDealTargetParity(t *testing.T) { checkScalarParity(t, "target") }
