// SPDX-License-Identifier: Apache-2.0

package referee

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/action-state-group/agent-action-capsule/go/evidenceresult"
)

// resultOf is the smallest requirement-claim Result that AAC's check accepts,
// with edit applied to its decoded form.
func resultOf(t *testing.T, edit func(doc object)) object {
	t.Helper()
	const text = `{"result_version":"evidence-result-v0","generated_at":"2026-11-01T12:00:00Z",
	"claims":[{"id":"c1","contract_ref":"ec:t@1","requirement_ref":"r1","verdict":"met","sufficiency":"SATISFIED",
	"grade":"self-attested","tier":"recomputed","evidence":[{"digest_alg":"SHA-256","digest":"` + "f8903616289dfc88f4dced0668107ae54d1449c8147eb7b395cf26b5f7048234" + `"}],"proofs":[],
	"presentation":{"kind":"disclosure","status":"SATISFIED","evidence":[{"digest_alg":"SHA-256","digest":"f8903616289dfc88f4dced0668107ae54d1449c8147eb7b395cf26b5f7048234"}]}}],
	"aggregate":{"buckets":{"met":["c1"],"not_met":[],"not_evaluable":[]},"coverage":{"evaluated_population":1,"unknown_count":0,"excluded_not_applicable":0}}}`
	v, _, err := decode([]byte(text))
	if err != nil {
		t.Fatal(err)
	}
	doc := v.(object)
	if edit != nil {
		edit(doc)
	}
	return doc
}

func TestCheckResultIsAACs(t *testing.T) {
	if err := checkResult(resultOf(t, nil)); err != nil {
		t.Fatalf("want ok, got %v", err)
	}
	// The result is refused exactly as evidenceresult.Check refuses it.
	for name, edit := range map[string]func(object){
		"empty evidence": func(d object) { d["claims"].([]any)[0].(object)["evidence"] = []any{} },
		"unknown member": func(d object) { d["claims"].([]any)[0].(object)["x"] = 1 },
		"claim in no bucket": func(d object) {
			d["aggregate"].(object)["buckets"].(object)["met"] = []any{}
		},
		"close claim without its body": func(d object) {
			d["claims"].([]any)[0].(object)["type"] = evidenceresult.TypeClose
		},
	} {
		t.Run(name, func(t *testing.T) {
			err := checkResult(resultOf(t, edit))
			var aac *evidenceresult.Error
			if !errors.As(err, &aac) {
				t.Fatalf("want an *evidenceresult.Error, got %v", err)
			}
		})
	}
}

// An AAC-only member, which the older vendored schema refused, is accepted.
func TestCheckResultAcceptsAACOnlyMembers(t *testing.T) {
	doc := resultOf(t, func(d object) {
		d["claims"].([]any)[0].(object)["applies_from"] = "2026-01-01"
	})
	if err := checkResult(doc); err != nil {
		t.Fatalf("applies_from in the past: %v", err)
	}
}

// A finding reaches the caller as result_invalid, with the finding in Detail.
func TestAACFindingIsResultInvalid(t *testing.T) {
	doc := resultOf(t, func(d object) { d["claims"].([]any)[0].(object)["evidence"] = []any{} })
	err := checkResult(doc)
	var re *Error
	if errors.As(err, &re) {
		t.Fatalf("checkResult returns AAC's error, VerifyResult maps it: %v", err)
	}
	if err == nil || !strings.Contains(err.Error(), "evidence-result-v0") {
		t.Fatalf("want an AAC finding, got %v", err)
	}
}

// The out_of_scope verdict, its bucket and the member that allows it are AAC's
// and were refused by the older vendored schema.
func TestCheckResultAcceptsOutOfScope(t *testing.T) {
	doc := resultOf(t, func(d object) {
		claim := d["claims"].([]any)[0].(object)
		claim["verdict"] = "out_of_scope"
		claim["type"] = evidenceresult.TypeRequirement
		claim["out_of_scope_allowed"] = true
		buckets := d["aggregate"].(object)["buckets"].(object)
		buckets["met"] = []any{}
		buckets["out_of_scope"] = []any{"c1"}
	})
	if err := checkResult(doc); err != nil {
		t.Fatalf("want ok, got %v", err)
	}
}

func TestCheckResultRefusesANonObject(t *testing.T) {
	var re *Error
	if err := checkResult([]any{}); !errors.As(err, &re) || re.Code != CodeResultInvalid {
		t.Fatalf("want result_invalid, got %v", err)
	}
}

// The Results the frozen Python judge seals for its lab faults are accepted:
// moving to AAC's check changes no verdict on anything the judge emits.
func TestJudgeResultsAreAccepted(t *testing.T) {
	f, err := os.Open("../../conformance-vectors/referee/v0/judge-results.json")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<24)
	n := 0
	for sc.Scan() {
		var r struct {
			Name string          `json:"name"`
			Doc  json.RawMessage `json:"doc"`
		}
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			t.Fatal(err)
		}
		v, tainted, err := decode(r.Doc)
		if err != nil || tainted {
			t.Fatalf("%s: %v", r.Name, err)
		}
		if err := checkResult(v); err != nil {
			t.Errorf("%s: %v", r.Name, err)
		}
		n++
	}
	if n != 6 {
		t.Fatalf("want 6 judge results, got %d", n)
	}
}
