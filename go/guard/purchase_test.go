package guard

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
	"gopkg.in/yaml.v3"
)

// The purchase limits are the vendored caps definition's own values, read
// here from its YAML file independently of the engine's decoder: the
// closing message states what the pack sets, never a typed figure.
func TestPurchaseLimitsAreThePacksCaps(t *testing.T) {
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	got, err := PurchaseLimits(p)
	if err != nil {
		t.Fatal(err)
	}
	w, _ := p.Wicket("caps")
	var def struct {
		Config struct {
			FoldID         string           `yaml:"fold_id"`
			CapsMinor      map[string]int64 `yaml:"caps_minor"`
			PerActionMinor map[string]int64 `yaml:"per_action_minor"`
		} `yaml:"config"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "packs", "everyday-0.3.7", "wickets", "caps.v5.1.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if err := yaml.Unmarshal(raw, &def); err != nil {
		t.Fatal(err)
	}
	if w.ID != "caps/5.1.0" {
		t.Fatalf("the vendored pack cites %s; read that definition's file here", w.ID)
	}
	fold, _ := p.Fold(def.Config.FoldID)
	want := PurchaseLimit{PerActionMinor: def.Config.PerActionMinor[PurchaseClass], WindowMinor: def.Config.CapsMinor[PurchaseClass], WindowDuration: fold.WindowDuration}
	if got != want || want.PerActionMinor == 0 || want.WindowMinor == 0 {
		t.Fatalf("PurchaseLimits = %+v, the caps file says %+v", got, want)
	}
}

// A pack with no caps check sets no spending limit, and none is stated.
func TestPurchaseLimitsRefuseAPackWithoutCaps(t *testing.T) {
	p, err := pack.LoadSeller012()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := PurchaseLimits(p); err == nil {
		t.Fatal("a pack citing no caps check yielded spending limits")
	}
}

// Each input notCheckedOnPurchases names is one the deal-record reader never
// takes: set on a sealed purchase record's body (its digest recomputed so the
// header binds it), the decoded action still lacks it. Once the reader takes
// one, this fails and the entry must go.
func TestNotCheckedOnPurchasesInputsAreNeverRead(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "records", "pay-check-record-2000.json"))
	if err != nil {
		t.Fatal(err)
	}
	for check, input := range notCheckedOnPurchases {
		edited, err := withBodyField(raw, input, "set-on-the-record")
		if err != nil {
			t.Fatal(err)
		}
		a, err := DecodeRecord(edited, "")
		if err != nil {
			t.Fatalf("%s: %v", check, err)
		}
		decoded, err := json.Marshal(a)
		if err != nil {
			t.Fatal(err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(decoded, &fields); err != nil {
			t.Fatal(err)
		}
		if _, read := fields[input]; read {
			t.Errorf("%s: the deal-record reader now takes %s; drop it from notCheckedOnPurchases", check, input)
		}
	}
}

// The rules a purchase cannot make fail are those citing a listed check:
// in asg/everyday/0.3.7, r07 and r19, as the v0.0.10 MANIFEST rules them,
// and r23, as ruled.
func TestNotCheckedOnPurchasesRules(t *testing.T) {
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	got := NotCheckedOnPurchases(p)
	want := map[string]bool{"r07-subscription-or-free-trial": true, "r19-counterparty-identity-changed": true, "r23-secrets-and-authentication": true}
	if len(got) != len(want) {
		t.Fatalf("NotCheckedOnPurchases = %v, want %v", got, want)
	}
	for id := range want {
		if !got[id] {
			t.Errorf("%s is missing from %v", id, got)
		}
	}
}

// withBodyField is the record raw with body[name] set to value, its
// agent_input digest recomputed so the header binds the new body. The
// record must be a purchase.
func withBodyField(raw []byte, name, value string) ([]byte, error) {
	var rec map[string]json.RawMessage
	if err := json.Unmarshal(raw, &rec); err != nil {
		return nil, err
	}
	var ai map[string]json.RawMessage
	if err := json.Unmarshal(rec["agent_input"], &ai); err != nil {
		return nil, err
	}
	var body map[string]json.RawMessage
	if err := json.Unmarshal(ai["body"], &body); err != nil {
		return nil, err
	}
	var class string
	if err := json.Unmarshal(body["action_class"], &class); err != nil || class != PurchaseClass {
		return nil, fmt.Errorf("the fixture is a %q, not a purchase", class)
	}
	var err error
	if body[name], err = json.Marshal(value); err != nil {
		return nil, err
	}
	if ai["body"], err = json.Marshal(body); err != nil {
		return nil, err
	}
	if rec["agent_input"], err = json.Marshal(ai); err != nil {
		return nil, err
	}
	v, err := canon.FromJSON(rec["agent_input"])
	if err != nil {
		return nil, err
	}
	digest, err := canon.Digest(v)
	if err != nil {
		return nil, err
	}
	var ma, ca map[string]json.RawMessage
	if err := json.Unmarshal(rec["model_attestation"], &ma); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(ma["compute_attestation"], &ca); err != nil {
		return nil, err
	}
	if ca["agent_input_digest"], err = json.Marshal(digest); err != nil {
		return nil, err
	}
	if ma["compute_attestation"], err = json.Marshal(ca); err != nil {
		return nil, err
	}
	if rec["model_attestation"], err = json.Marshal(ma); err != nil {
		return nil, err
	}
	return json.Marshal(rec)
}
