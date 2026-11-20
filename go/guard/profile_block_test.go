package guard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Every record of evidencebook's released x-deal-v1 Appendix A deal (v0.18.1,
// testdata/x-deal-v1-fixtures) is read as a v1 block, with the record type the
// guard knows for its step, and names its deal.
func TestProfileBlockReadsEveryAppendixARecord(t *testing.T) {
	paths, err := filepath.Glob("testdata/x-deal-v1-fixtures/*/*.json")
	if err != nil || len(paths) < 30 {
		t.Fatalf("fixtures: %v, %d files", err, len(paths))
	}
	for _, p := range paths {
		var fx struct {
			Record json.RawMessage `json:"record"`
		}
		raw, err := os.ReadFile(p)
		if err != nil || json.Unmarshal(raw, &fx) != nil {
			t.Fatalf("%s: %v", p, err)
		}
		block, version := profileBlockOfInput(fx.Record)
		if block == nil || version != profileV1 {
			t.Errorf("%s: read as %q, block %v", p, version, block)
			continue
		}
		if why := unreadableProfile(fx.Record); why != "" {
			t.Errorf("%s: unreadable: %s", p, why)
		}
		var own struct {
			Block struct {
				RecordType string `json:"record_type"`
			} `json:"x-deal-v1"`
		}
		_ = json.Unmarshal(fx.Record, &own)
		want, mapped := v1ToV0Type[own.Block.RecordType]
		if !mapped {
			want = own.Block.RecordType
		}
		var got string
		_ = json.Unmarshal(block["record_type"], &got)
		if got != want {
			t.Errorf("%s: record_type %q, want %q", p, got, want)
		}
		if deal, src, conflict := dealOf(fx.Record); strings.Contains(p, "-book") && (deal != "deal-6a1f0c93b27d4e58" || src != "x-deal-v1.deal_id" || conflict) {
			t.Errorf("%s: deal %q from %q, conflict %v", p, deal, src, conflict)
		}
	}
}

func TestProfileBlockVersions(t *testing.T) {
	for name, tc := range map[string]struct {
		in      string
		version string
		why     bool
	}{
		"none":                    {`{"body":{}}`, "", false},
		"v0 as it was":            {`{"x-deal-v0":{"record_type":"check"}}`, profileV0, false},
		"v0 without a profile":    {`{"x-deal-v0":{"deal_id":"d"}}`, profileV0, false},
		"v1":                      {`{"x-deal-v1":{"profile":"x-deal-v1","record_type":"check"}}`, profileV1, false},
		"v1 not naming itself":    {`{"x-deal-v1":{"profile":"x-deal-v0","record_type":"check"}}`, "", true},
		"v1 with no profile":      {`{"x-deal-v1":{"record_type":"check"}}`, "", true},
		"v1 not an object":        {`{"x-deal-v1":"x"}`, "", true},
		"both blocks":             {`{"x-deal-v0":{},"x-deal-v1":{"profile":"x-deal-v1"}}`, "", true},
		"unknown version":         {`{"x-deal-v2":{"profile":"x-deal-v2"}}`, "", true},
		"two unknown versions":    {`{"x-deal-v2":{},"x-deal-v3":{}}`, "", true},
		"a member that is no one": {`{"x-deal-vx":{}}`, "", false},
	} {
		_, version := profileBlockOfInput(json.RawMessage(tc.in))
		if version != tc.version {
			t.Errorf("%s: version %q, want %q", name, version, tc.version)
		}
		if got := unreadableProfile(json.RawMessage(tc.in)) != ""; got != tc.why {
			t.Errorf("%s: unreadable %v, want %v", name, got, tc.why)
		}
	}
}

// The v1 types that name an act, or a change, are read as the v0 types the
// guard's readers know; every other v1 type stays its own name, which is not
// a check, so it states no act.
func TestProfileBlockTypeMapping(t *testing.T) {
	for v1, want := range map[string]string{"perform": "action", "settle": "action", "detail_change": "change",
		"check": "check", "claim": "claim", "message": "message", "statement": "statement", "ruling": "ruling", "agree": "agree"} {
		block, _ := profileBlockOfInput(json.RawMessage(`{"x-deal-v1":{"profile":"x-deal-v1","record_type":"` + v1 + `"}}`))
		var got string
		_ = json.Unmarshal(block["record_type"], &got)
		if got != want {
			t.Errorf("%s: read as %q, want %q", v1, got, want)
		}
	}
}

func TestV1ActRead(t *testing.T) {
	agentInput := json.RawMessage(`{"x-deal-v1":{"profile":"x-deal-v1","record_type":"settle"},"body":{"leg":"payer","reverses":{"type":"deal-record","digest_alg":"SHA-256","digest":"` + strings.Repeat("a", 64) + `"}}}`)
	a := readV1Act(agentInput, json.RawMessage(`{"settlement":{"version":"0","leg":"payer","amount":{"value":"2000","assetCode":"USD","assetScale":2}}}`))
	if a.malformed != "" || a.amount == nil || *a.amount != 2000 || *a.currency != "USD" || a.reverses == nil || *a.reverses != strings.Repeat("a", 64) {
		t.Fatalf("read %+v", a)
	}
	if got := a.act(); got.Direction == nil || *got.Direction != "out" || string(got.SpendMinor) != "2000" {
		t.Errorf("payer leg: %+v", got)
	}
	for name, capsule := range map[string]string{"no member": `{}`, "no code": `{"settlement":{"amount":{"value":"1"}}}`,
		"negative": `{"settlement":{"amount":{"value":"-1","assetCode":"USD"}}}`, "leading zeros": `{"settlement":{"amount":{"value":"007","assetCode":"USD"}}}`, "plus sign": `{"settlement":{"amount":{"value":"+5","assetCode":"USD"}}}`,
		"not digits":   `{"settlement":{"amount":{"value":"1.5","assetCode":"USD"}}}`,
		"above 2^53-1": `{"settlement":{"amount":{"value":"9007199254740992","assetCode":"USD"}}}`} {
		if got := readV1Act(agentInput, json.RawMessage(capsule)); got.malformed == "" {
			t.Errorf("%s: read as %+v", name, got)
		}
	}
	if got := readV1Act(agentInput, json.RawMessage(`{"settlement":{"amount":{"value":"9007199254740991","assetCode":"USD"}}}`)); got.malformed != "" || *got.amount != 1<<53-1 {
		t.Errorf("2^53-1: read as %+v", got)
	}
	perform := readV1Act(json.RawMessage(`{"x-deal-v1":{"profile":"x-deal-v1","record_type":"perform"},"body":{}}`), json.RawMessage(`{}`))
	if perform.malformed != "" || perform.act().AmountMinor != nil {
		t.Errorf("perform: %+v", perform)
	}
}

func TestStatementOfReadsAV1Claim(t *testing.T) {
	for _, profile := range []string{"x-deal-v0", "x-deal-v1"} {
		in := `{"` + profile + `":{"profile":"` + profile + `","record_type":"claim","deal_id":"deal-6a1f0c93b27d4e58","at":"2026-11-01T10:00:00Z"},"body":{"source_kind":"agent","class":"delivery_date"}}`
		digest := agentInputDigest(json.RawMessage(in))
		capsule := `{"capsule_id":"c1","operator":"op","model_attestation":{"compute_attestation":{"agent_input_digest":"` + digest + `"}}}`
		r, ok := StatementOf(json.RawMessage(capsule), json.RawMessage(in))
		if !ok || r.Stated == nil || r.Stated.DealID != "deal-6a1f0c93b27d4e58" || r.Timestamp != "2026-11-01T10:00:00Z" {
			t.Errorf("%s: %+v %v", profile, r, ok)
		}
	}
}

// A checked record whose profile cannot be read is read from its header alone
// (capsule-engine#309), as Python's action_for_record reads it: unevaluable,
// in no known deal, and with none of what its body states.
func TestUnreadableProfileCheckIsReadFromItsHeaderAlone(t *testing.T) {
	for _, name := range []string{"v1-check-two-blocks", "v1-check-unknown-version", "v1-check-block-names-other", "v1-check-extra-block"} {
		raw, err := os.ReadFile("testdata/x-deal-v1-vectors/inputs/" + name + ".input.json")
		if err != nil {
			t.Fatal(err)
		}
		a, _, err := DecodeCheckInput(raw)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if len(a.Unevaluable()) == 0 || !a.dealIDConflict || a.ActionClass != nil || a.AmountMinor != nil || a.Target != nil {
			t.Errorf("%s: unevaluable %v, in no deal %v, class %v, amount %v, target %v", name, a.Unevaluable(), a.dealIDConflict, a.ActionClass, a.AmountMinor, a.Target)
		}
	}
}
