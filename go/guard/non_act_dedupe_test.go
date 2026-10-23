package guard

import (
	"encoding/json"
	"testing"
)

// A DEAL RECORD THAT STATES NO ACT IS NOT DEDUPED (capsule-engine #107, in
// 5300e0b): only a check states an act; a baseline, verdict, approval,
// intent, or the action step that carries a check out does not, read from
// the record's sealed x-deal-v0.record_type. Its dedupe is n/a, out of
// scope, so it is never refused as a repeat of the deal's own records,
// while each check is still deduped. From capsule-engine #121 (97a2aff) the
// replay gives such a record no decision at all, so it never reaches the
// ledger a later check reads; checked alone, its dedupe is still n/a. Held
// against the engine's replay of two capsulectl deal bundles
// (testdata/parity/generate_non_act_dedupe.py): each record, in order, gives
// whether the replay decided it, and the engine's dedupe result and evidence
// digest.
func TestANonActDealRecordIsNotDeduped(t *testing.T) {
	var py struct {
		CapsuleEngineCommit string `json:"capsule_engine_commit"`
		Bundles             map[string][]struct {
			CapsuleID            string `json:"capsule_id"`
			SealedRecordType     string `json:"sealed_record_type"`
			Replay               string `json:"replay"`
			DedupeResult         string `json:"dedupe_result"`
			DedupeEvidenceDigest string `json:"dedupe_evidence_digest"`
		} `json:"bundles"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "non-act-dedupe-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	if py.CapsuleEngineCommit != parity(t, fixtures037).CapsuleEngineCommit || len(py.Bundles) != 2 {
		t.Fatalf("%d bundles at %s", len(py.Bundles), py.CapsuleEngineCommit)
	}
	e, _ := everydayEngine(t)
	nonActs := 0
	for name, rows := range py.Bundles {
		bundle := readFile(t, "testdata", "parity", "deal-bundles", name+".bundle.json")
		var ledger []Record
		for i, row := range rows {
			a, err := DecodeRecord(bundle, row.CapsuleID)
			if err != nil {
				t.Fatalf("%s row %d: %v", name, i, err)
			}
			// A record the replay gives no decision is checked alone, on an
			// empty ledger, as the engine's test reads its dedupe.
			isAct := row.SealedRecordType == "check"
			if want := map[bool]string{true: "decision", false: "no_decision"}[isAct]; row.Replay != want {
				t.Fatalf("%s row %d (%s): replay %q, want %q", name, i, row.SealedRecordType, row.Replay, want)
			}
			against := ledger
			if !isAct {
				against = nil
			}
			d, err := e.Check(a, against)
			if err != nil {
				t.Fatalf("%s row %d: %v", name, i, err)
			}
			c, _ := d.Constraint("dedupe")
			digest, _, err := c.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			if string(c.Result) != row.DedupeResult || digest != row.DedupeEvidenceDigest {
				t.Errorf("%s row %d (%s): dedupe %s %s, engine %s %s", name, i, row.SealedRecordType, c.Result, digest,
					row.DedupeResult, row.DedupeEvidenceDigest)
			}
			if !isAct {
				nonActs++
				if rule := ruleResultFor(e, a, d, "r27-no-commitment-beyond-task-bounds"); rule.Verdict != VerdictNotApplicable ||
					rule.Reason != "the record states no act" {
					t.Errorf("%s row %d (%s): r27 %s %q", name, i, row.SealedRecordType, rule.Verdict, rule.Reason)
				}
			}
			if !isAct {
				continue
			}
			r := RecordFor(a, d)
			r.CapsuleID, r.DryRun = row.CapsuleID, boolp(true)
			ledger = append(ledger, r)
		}
	}
	if nonActs < 10 {
		t.Fatalf("%d non-act records exercised", nonActs)
	}
}

// ruleResultFor is the report's result for one rule.
func ruleResultFor(e *Engine, a Action, d Decision, rule string) RuleResult {
	for _, r := range e.Report(a, d).Rules {
		if r.Rule == rule {
			return r
		}
	}
	return RuleResult{}
}
