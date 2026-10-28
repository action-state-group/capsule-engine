package guard

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// equivalenceKeyVectors is the engine side's vectors.json for
// the sealed equivalence key (capsule-engine #134, dbdaca12).
type equivalenceKeyVectors struct {
	Field      string            `json:"field"`
	KeyDigests map[string]string `json:"key_digests"`
	Scenarios  map[string][]struct {
		EquivalenceKey *string         `json:"equivalence_key"`
		ActionKey      string          `json:"action_key"`
		CapsuleKey     string          `json:"capsule_key"`
		Dedupe         string          `json:"dedupe"`
		Outcome        string          `json:"outcome"`
		Capsule        json.RawMessage `json:"capsule"`
	} `json:"scenarios"`
}

func equivalenceKeyVectorsOf(t *testing.T) equivalenceKeyVectors {
	t.Helper()
	return decodeJSON[equivalenceKeyVectors](t, readFile(t, "testdata", "dedupe-equivalence-key-vectors", "vectors.json"))
}

// keyedPay is gen.py's pay(n, **f): the n-th payment, with the caller's
// equivalence key when key is set.
func keyedPay(t *testing.T, n int, key *string) Action {
	t.Helper()
	body := fmt.Sprintf(`{"verb":"pay","operator":"dedupe-equivalence-key","developer":"assistant@v1","action_class":"money.transfer",`+
		`"currency":"EUR","target":"payee/garden-centre","amount_minor":1250,"action_id":"pay/dedupe-equivalence-key-%d",`+
		`"timestamp":"2026-10-09T12:%02d:00Z"`, n, n)
	if key != nil {
		body += fmt.Sprintf(`,"equivalence_key":%q`, *key)
	}
	a, err := DecodeAction(strings.NewReader(body + "}"))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// A CALLER'S KEY IS SEALED AS ITS DIGEST, AND A RETRY UNDER IT IS REFUSED
// (capsule-engine #134): the key the action is matched on is
// json_digest(equivalence_key), sealed as asg_payload.equivalence_key_digest;
// a ledger capsule is keyed on that digest when it carries one, else on the
// default formula. Held against the engine's decisions step by step: the
// action's key, the key of the capsule its decision leaves (read back from
// the engine's own capsule, and from the record this port leaves), the
// dedupe result, and the dedupe evidence byte for byte (its digest equals
// the engine capsule's evidence_digest).
func TestEquivalenceKeySealedAsDigestMatchesTheEngine(t *testing.T) {
	v := equivalenceKeyVectorsOf(t)
	if v.Field != "asg_payload.equivalence_key_digest" || len(v.Scenarios) != 4 {
		t.Fatalf("vectors: field %q, %d scenarios", v.Field, len(v.Scenarios))
	}
	for key, want := range v.KeyDigests {
		if got, err := equivalenceKeyForAction(keyedPay(t, 1, &key)); err != nil || got != want {
			t.Errorf("json_digest(%q): %s (%v), engine %s", key, got, err, want)
		}
	}
	e, _ := everydayEngine(t)
	for name, steps := range v.Scenarios {
		var engineLedger, ownLedger []Record
		for i, step := range steps {
			a := keyedPay(t, i+1, step.EquivalenceKey)
			key, err := equivalenceKeyForAction(a)
			if err != nil {
				t.Fatal(err)
			}
			if key != step.ActionKey {
				t.Errorf("%s step %d: action key %s, engine %s", name, i, key, step.ActionKey)
			}
			var line bytes.Buffer
			if err := json.Compact(&line, step.Capsule); err != nil {
				t.Fatal(err)
			}
			recs, err := readCapsules(&line)
			if err != nil || len(recs) != 1 {
				t.Fatalf("%s step %d: %d records, %v", name, i, len(recs), err)
			}
			if rk, err := equivalenceKeyForRecord(recs[0]); err != nil || rk != step.CapsuleKey {
				t.Errorf("%s step %d: capsule key %s (%v), engine %s", name, i, rk, err, step.CapsuleKey)
			}
			// Checked twice: over the engine's own capsules, and over the
			// records this port's decisions leave (each under the engine
			// capsule's id, so a match names the same record).
			for _, own := range []bool{false, true} {
				ledger := engineLedger
				if own {
					ledger = ownLedger
				}
				d, err := e.Check(a, MemoryHistory(ledger))
				if err != nil {
					t.Fatal(err)
				}
				dd, _ := d.Constraint("dedupe")
				if string(dd.Result) != step.Dedupe {
					t.Errorf("%s step %d (own %v): dedupe %s, engine %s", name, i, own, dd.Result, step.Dedupe)
				}
				got, ok, err := dd.EvidenceDigest()
				if want := constraintDigest(t, step.Capsule, "dedupe"); err != nil || !ok || got != want {
					t.Errorf("%s step %d (own %v): dedupe evidence %s (%v %v), engine %s", name, i, own, got, ok, err, want)
				}
				if own {
					r := RecordFor(a, d)
					r.CapsuleID = recs[0].CapsuleID
					if rk, err := equivalenceKeyForRecord(r); err != nil || rk != step.CapsuleKey {
						t.Errorf("%s step %d: own record key %s (%v), engine %s", name, i, rk, err, step.CapsuleKey)
					}
					ownLedger = append(ownLedger, r)
				}
			}
			engineLedger = append(engineLedger, recs[0])
		}
	}
	// The cases the vectors exist for, named so a regenerated file that lost
	// one fails here.
	for name, want := range map[string][]string{
		"retry_same_key_denied":          {"pass", "fail"},
		"two_keys_distinct":              {"pass", "pass"},
		"keyed_then_unkeyed_never_match": {"pass", "pass"},
		"unkeyed_repeat_denied":          {"pass", "fail"},
	} {
		var got []string
		for _, s := range v.Scenarios[name] {
			got = append(got, s.Dedupe)
		}
		if strings.Join(got, ",") != strings.Join(want, ",") {
			t.Errorf("%s: engine dedupe %v, want %v", name, got, want)
		}
	}
}

// constraintDigest is the evidence_digest of the named constraint in an
// engine capsule.
func constraintDigest(t *testing.T, capsule json.RawMessage, id string) string {
	t.Helper()
	var c struct {
		Constraints []struct {
			ID             string `json:"id"`
			EvidenceDigest string `json:"evidence_digest"`
		} `json:"constraints"`
	}
	if err := json.Unmarshal(capsule, &c); err != nil {
		t.Fatal(err)
	}
	for _, k := range c.Constraints {
		if k.ID == id {
			return k.EvidenceDigest
		}
	}
	t.Fatalf("the capsule has no %s constraint", id)
	return ""
}

// The raw key is never what is matched or recorded: a key equal to the
// digest of the default formula of an unkeyed record does not match it,
// because the key is digested before comparison (#134's PR body).
func TestEquivalenceKeyEqualToAFormulaNeverMatchesAnUnkeyedRecord(t *testing.T) {
	e, _ := everydayEngine(t)
	unkeyed := keyedPay(t, 1, nil)
	formula, err := equivalenceKeyForAction(unkeyed)
	if err != nil {
		t.Fatal(err)
	}
	d, err := e.Check(unkeyed, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := RecordFor(unkeyed, d)
	r.CapsuleID = "unkeyed"
	if r.EquivalenceKeyDigest != nil {
		t.Fatalf("an unkeyed decision records a key digest %s", *r.EquivalenceKeyDigest)
	}
	retry := keyedPay(t, 2, &formula)
	d, err = e.Check(retry, MemoryHistory([]Record{r}))
	if err != nil {
		t.Fatal(err)
	}
	if dd, _ := d.Constraint("dedupe"); dd.Result != Pass {
		t.Errorf("a key equal to the formula's digest: dedupe %s, want pass", dd.Result)
	}
	if got := RecordFor(retry, d).EquivalenceKeyDigest; got == nil || *got == formula {
		t.Errorf("the record seals %v, not the key's digest", got)
	}
}

// PROVENANCE.json pins each vendored file by its sha256 (the set has no
// MANIFEST), at the engine commit the vectors were regenerated at.
func TestEquivalenceKeyVectorsAreTheVendoredBytes(t *testing.T) {
	prov := decodeJSON[struct {
		EngineCommit string `json:"engine_commit"`
		Files        map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}](t, readFile(t, "testdata", "dedupe-equivalence-key-vectors", "PROVENANCE.json"))
	if prov.EngineCommit != "dbdaca1254af169a8a086ae4f087943511f712db" || len(prov.Files) != 2 {
		t.Fatalf("provenance: %s, %d files", prov.EngineCommit, len(prov.Files))
	}
	for name, f := range prov.Files {
		sum := sha256.Sum256(readFile(t, "testdata", "dedupe-equivalence-key-vectors", name))
		if hex.EncodeToString(sum[:]) != f.SHA256 {
			t.Errorf("%s: sha256 %x, pinned %s", name, sum, f.SHA256)
		}
	}
}
