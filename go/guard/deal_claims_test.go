package guard

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// dealClaimsCases are capsule-engine #140's record.deal_claims reader at
// b455063a on one real seller check input (gen_deal_claims_cases.py): each
// case the deal_claims given, the statements deal_claim_statements writes,
// and the action's ignored inputs.
var dealClaimsCases = filepath.Join("testdata", "deal-claims-cases")

type dealClaimsCase struct {
	Case          string          `json:"case"`
	DealClaims    json.RawMessage `json:"deal_claims"`
	RawStatements json.RawMessage `json:"statements"`
	Ignored       bool            `json:"ignored"`
	IgnoredInputs []string        `json:"ignored_inputs"`
}

// dealClaimsEnvelope is the envelope the cases change: a-commit-1900 with
// the deal's claims beside its record, as the engine's _given gives it.
func dealClaimsEnvelope(t *testing.T) []byte {
	t.Helper()
	return readFile(t, atB455063a, "live-history", "given", "a-commit-1900.disposition-accept-record-deal-claims.json")
}

// withDealClaims is the envelope with record.deal_claims set to claims, or
// left out when claims is the case's "<absent>".
func withDealClaims(t *testing.T, claims json.RawMessage) []byte {
	t.Helper()
	var env map[string]json.RawMessage
	if err := json.Unmarshal(dealClaimsEnvelope(t), &env); err != nil {
		t.Fatal(err)
	}
	var record map[string]json.RawMessage
	if err := json.Unmarshal(env["record"], &record); err != nil {
		t.Fatal(err)
	}
	if string(claims) == `"<absent>"` {
		delete(record, dealClaimsInput)
	} else {
		record[dealClaimsInput] = claims
	}
	b, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	env["record"] = b
	out, err := json.Marshal(env)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestDealClaimsCasesAreVendoredAsRecorded(t *testing.T) {
	var prov struct {
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, dealClaimsCases, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	var onDisk []string
	err := fs.WalkDir(os.DirFS(dealClaimsCases), ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || p == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range onDisk {
		f, ok := prov.Files[name]
		if !ok {
			t.Errorf("%s is not recorded", name)
			continue
		}
		if got := fileSHA256Hex(readFile(t, dealClaimsCases, name)); got != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, got, f.SHA256)
		}
	}
	if len(onDisk) != len(prov.Files) || len(onDisk) != 2 {
		t.Errorf("vendored %v, provenance records %d", onDisk, len(prov.Files))
	}
}

// CROSS-IMPLEMENTATION: every case the engine decides, with NO known
// difference: the statement records written for the claims beside the
// checked record (each record whole, its capsule_id included), and the
// action's ignored inputs.
func TestDealClaimsAgreeWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Cases []dealClaimsCase `json:"cases"`
	}](t, readFile(t, dealClaimsCases, "deal-claims-cases.json"))
	if len(v.Cases) != 34 {
		t.Fatalf("%d cases", len(v.Cases))
	}
	for _, c := range v.Cases {
		a, h, err := DecodeCheckInput(withDealClaims(t, c.DealClaims))
		if err != nil {
			t.Fatalf("%s: %v", c.Case, err)
		}
		var want []json.RawMessage
		if err := json.Unmarshal(c.RawStatements, &want); err != nil {
			t.Fatal(err)
		}
		if len(h.claimed) != len(want) {
			t.Errorf("%s: Go writes %d statements, the engine %d", c.Case, len(h.claimed), len(want))
			continue
		}
		for i, w := range want {
			if g := statementJSON(t, h.claimed[i]); jsonDigest(t, g) != jsonDigest(t, w) {
				t.Errorf("%s: Go %s, engine %s", c.Case, g, w)
			}
		}
		if !slices.Equal(a.ignoredInputs, c.IgnoredInputs) {
			t.Errorf("%s: Go ignores %v, the engine %v", c.Case, a.ignoredInputs, c.IgnoredInputs)
		}
	}
}

// A claim the history already gave is one statement, however each path
// dates it (live_history.py claim_of; the engine's
// test_deal_claims_and_the_history_claim_are_one_statement): the deal's
// claims beside the record, and the same claim in the history.
func TestADealClaimTheHistoryGaveIsOneStatement(t *testing.T) {
	for _, offset := range []string{"Z", "+00:00"} {
		var env, inHistory map[string]json.RawMessage
		if err := json.Unmarshal(readFile(t, atB455063a, "live-history", "given", "a-commit-again-1900.disposition-accept-record-deal-claims.json"), &env); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(readFile(t, atB455063a, "live-history", "given", "a-commit-again-1900.disposition-accept-claims-in-history.json"), &inHistory); err != nil {
			t.Fatal(err)
		}
		env["history"] = inHistory["history"]
		var record map[string]json.RawMessage
		if err := json.Unmarshal(env["record"], &record); err != nil {
			t.Fatal(err)
		}
		var claims []map[string]json.RawMessage
		if err := json.Unmarshal(record[dealClaimsInput], &claims); err != nil || len(claims) != 1 {
			t.Fatalf("%d deal claims: %v", len(claims), err)
		}
		var at string
		if err := json.Unmarshal(claims[0]["at"], &at); err != nil || !strings.HasSuffix(at, "Z") {
			t.Fatalf("at %q", at)
		}
		claims[0]["at"] = json.RawMessage(`"` + strings.TrimSuffix(at, "Z") + offset + `"`)
		record[dealClaimsInput] = mustJSON(t, claims)
		env["record"] = mustJSON(t, record)
		a, h, err := DecodeCheckInput(mustJSON(t, env))
		if err != nil {
			t.Fatal(err)
		}
		var written []string
		for _, r := range h.records() {
			if r.Stated != nil && r.Stated.DealID == *a.DealID {
				written = append(written, r.Stated.Claim)
			}
		}
		if len(written) != 1 || len(h.claimed) != 0 {
			t.Errorf("at ...%s: the deal's claim is written %d times (%d beside the record)", offset, len(written), len(h.claimed))
		}
	}
}

// mustJSON is v, a decoded JSON object or list of objects, as JSON again.
func mustJSON[T map[string]json.RawMessage | []map[string]json.RawMessage](t *testing.T, v T) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// The checks read the deal's claims after the history's records, as the
// engine's history ledger writes them (live_history.py: deal_claim_statements
// after the dated entries).
func TestDealClaimsAreReadAfterTheHistory(t *testing.T) {
	_, h, err := DecodeCheckInput(readFile(t, atB455063a, "live-history", "given", "a-commit-again-1900.disposition-accept-record-deal-claims.json"))
	if err != nil {
		t.Fatal(err)
	}
	ledger := h.records()
	if len(h.Acts) == 0 || len(h.claimed) != 1 || len(ledger) != len(h.Acts)+1 {
		t.Fatalf("%d acts, %d claimed, ledger %d", len(h.Acts), len(h.claimed), len(ledger))
	}
	for i, r := range h.Acts {
		if ledger[i].CapsuleID != r.CapsuleID {
			t.Errorf("ledger[%d] is %s, the history's %s", i, ledger[i].CapsuleID, r.CapsuleID)
		}
	}
	if last := ledger[len(ledger)-1]; last.CapsuleID != h.claimed[0].CapsuleID || last.Stated == nil {
		t.Errorf("the ledger ends with %s, not the deal's claim", last.CapsuleID)
	}
}
