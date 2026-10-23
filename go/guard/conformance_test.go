package guard

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// The shared conformance vectors (docs/design/go-guard-module.md §5): each
// case's input, decided by this port, gives the decision the Python engine
// wrote for it (scripts/conformance_vectors.py). A case whose decision
// differs by design is pinned in KNOWN-DIFFERENCES.json, both sides' values
// with it, and fails when either side moves.

// conformanceRoot is the repository root, two directories above this package.
const conformanceRoot = "../.."

type conformanceSet struct {
	Source string            `json:"source"`
	Cases  []conformanceCase `json:"cases"`
}

type conformanceCase struct {
	Case        string              `json:"case"`
	Kind        string              `json:"kind"`
	Pack        string              `json:"pack"`
	Input       string              `json:"input"`
	InputSHA256 string              `json:"input_sha256"`
	Expected    conformanceDecision `json:"expected"`
}

type conformanceDecision struct {
	Error       string                  `json:"error,omitempty"`
	Outcome     string                  `json:"outcome,omitempty"`
	Constraints []conformanceConstraint `json:"constraints,omitempty"`
	// Steps are an action-ledger case's decisions, one per step.
	Steps []conformanceDecision `json:"steps,omitempty"`
	// CapsuleID is the capsule the Python engine sealed for a step. The
	// caller seals in Go, so the runner gives the record it appends this id.
	CapsuleID string `json:"capsule_id,omitempty"`
}

type conformanceConstraint struct {
	ID             string `json:"id"`
	Result         string `json:"result"`
	Method         string `json:"method"`
	EvidenceDigest string `json:"evidence_digest"`
	// Reason is an n/a's reason. Python writes one for every n/a; this port
	// keeps one only where it composes it (Pending, or an out-of-scope
	// note), and that one must be Python's.
	Reason string `json:"reason,omitempty"`
}

// knownDifference is one case Go decides differently by design: the Python
// side's expected decision is the vector's, and Go's is pinned here.
type knownDifference struct {
	Case   string              `json:"case"`
	Issue  string              `json:"issue"`
	Why    string              `json:"why"`
	Python conformanceDecision `json:"python"`
	Go     conformanceDecision `json:"go"`
}

func conformanceDir() string {
	return filepath.Join(conformanceRoot, "conformance-vectors", "guard", "v0")
}

func readConformanceSets(t *testing.T) map[string]conformanceSet {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(conformanceDir(), "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	sets := map[string]conformanceSet{}
	for _, p := range paths {
		if filepath.Base(p) == "KNOWN-DIFFERENCES.json" || filepath.Base(p) == "COVERAGE-EXEMPT.json" {
			continue
		}
		raw, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		var s conformanceSet
		if err := json.Unmarshal(raw, &s); err != nil {
			t.Fatalf("%s: %v", p, err)
		}
		sets[filepath.Base(p)] = s
	}
	if len(sets) == 0 {
		t.Fatal("no conformance vectors")
	}
	return sets
}

func readKnownDifferences(t *testing.T) map[string]knownDifference {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(conformanceDir(), "KNOWN-DIFFERENCES.json"))
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Cases []knownDifference `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	out := map[string]knownDifference{}
	for _, k := range file.Cases {
		if k.Issue == "" || k.Why == "" {
			t.Errorf("%s: a known difference names its issue and why", k.Case)
		}
		out[k.Case] = k
	}
	return out
}

// conformanceEngines are the engines over the embedded packs, by directory.
func conformanceEngines(t *testing.T) map[string]*Engine {
	t.Helper()
	out := map[string]*Engine{}
	for _, v := range pack.Embedded() {
		p, err := v.Load()
		if err != nil {
			t.Fatal(err)
		}
		e, err := NewEngine(p)
		if err != nil {
			t.Fatal(err)
		}
		out[v.Dir] = e
	}
	return out
}

// decideCase is this port's decision on one case, in the vectors' shape.
func decideCase(t *testing.T, engines map[string]*Engine, c conformanceCase) conformanceDecision {
	t.Helper()
	e, ok := engines[c.Pack]
	if !ok {
		t.Fatalf("%s: pack %s is not embedded", c.Case, c.Pack)
	}
	raw, err := os.ReadFile(filepath.Join(conformanceRoot, filepath.FromSlash(c.Input)))
	if err != nil {
		t.Fatal(err)
	}
	if sum := sha256.Sum256(raw); hex.EncodeToString(sum[:]) != c.InputSHA256 {
		t.Fatalf("%s: %s is not the input pinned", c.Case, c.Input)
	}
	switch c.Kind {
	case "check-input":
		a, h, err := DecodeCheckInput(raw)
		if err != nil {
			return conformanceDecision{Error: "decode: " + err.Error()}
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			return conformanceDecision{Error: "check: " + err.Error()}
		}
		return decisionShape(t, c.Case, d)
	case "action-ledger":
		var in struct {
			Steps []json.RawMessage `json:"steps"`
		}
		if err := json.Unmarshal(raw, &in); err != nil {
			t.Fatalf("%s: %v", c.Case, err)
		}
		var ledger []Record
		out := conformanceDecision{}
		for i, step := range in.Steps {
			a, err := DecodeAction(bytes.NewReader(step))
			if err != nil {
				return conformanceDecision{Error: "decode: " + err.Error()}
			}
			d, err := e.Check(a, ledger)
			if err != nil {
				return conformanceDecision{Error: "check: " + err.Error()}
			}
			got := decisionShape(t, c.Case, d)
			r := RecordFor(a, d)
			if i < len(c.Expected.Steps) {
				got.CapsuleID = c.Expected.Steps[i].CapsuleID
				r.CapsuleID = got.CapsuleID
			}
			out.Steps = append(out.Steps, got)
			ledger = append(ledger, r)
		}
		return out
	}
	t.Fatalf("%s: unknown kind %q", c.Case, c.Kind)
	return conformanceDecision{}
}

// decisionShape is a decision in the vectors' shape.
func decisionShape(t *testing.T, name string, d Decision) conformanceDecision {
	t.Helper()
	out := conformanceDecision{Outcome: d.Outcome}
	for _, cs := range d.Constraints {
		digest, _, err := cs.EvidenceDigest()
		if err != nil {
			t.Fatalf("%s: %s evidence: %v", name, cs.ID, err)
		}
		reason := ""
		if cs.Result == NotApplicable {
			reason = cs.pending
			if reason == "" {
				reason = cs.scopeNote
			}
		}
		out.Constraints = append(out.Constraints, conformanceConstraint{ID: cs.ID, Result: string(cs.Result), Method: cs.Method, EvidenceDigest: digest, Reason: reason})
	}
	return out
}

// errorStage is where a refusal happened: an exception's type for the
// engine, and "decode" or "check" for this port.
func errorStage(e string) string {
	stage, _, _ := strings.Cut(e, ":")
	return stage
}

// sameConstraint is whether two constraints agree. A Go side that keeps no
// reason for an n/a agrees with any.
func sameConstraint(g, p conformanceConstraint) bool {
	if g.Reason == "" {
		g.Reason = p.Reason
	}
	return g == p
}

// sameDecision is whether a's decision is b's. Across the two languages an
// error agrees with an error, since the sides name their refusals
// differently; a pin (exact) holds an error to its stage.
func sameDecision(a, b conformanceDecision) bool { return agrees(a, b, false) }

func agrees(a, b conformanceDecision, exact bool) bool {
	if a.Error != "" || b.Error != "" {
		if exact {
			return errorStage(a.Error) == errorStage(b.Error)
		}
		return a.Error != "" && b.Error != ""
	}
	if len(a.Steps) != len(b.Steps) {
		return false
	}
	for i := range a.Steps {
		if !agrees(a.Steps[i], b.Steps[i], exact) {
			return false
		}
	}
	return a.Outcome == b.Outcome && slices.EqualFunc(a.Constraints, b.Constraints, sameConstraint)
}

func describe(d conformanceDecision) string {
	raw, _ := json.Marshal(d)
	return string(raw)
}

func TestConformanceVectors(t *testing.T) {
	sets := readConformanceSets(t)
	known := readKnownDifferences(t)
	engines := conformanceEngines(t)
	seen := map[string]bool{}
	cases := 0
	for name, s := range sets {
		for _, c := range s.Cases {
			cases++
			id := name + ":" + c.Case
			got := decideCase(t, engines, c)
			if k, ok := known[id]; ok {
				seen[id] = true
				if !agrees(k.Python, c.Expected, true) {
					t.Errorf("%s: Python now decides %s, not the pinned %s: re-pin or close %s", id, describe(c.Expected), describe(k.Python), k.Issue)
				}
				if !agrees(got, k.Go, true) {
					t.Errorf("%s: Go now decides %s, not the pinned %s: re-pin or close %s", id, describe(got), describe(k.Go), k.Issue)
				}
				continue
			}
			if !sameDecision(got, c.Expected) {
				t.Errorf("%s: %s", id, difference(got, c.Expected))
			}
		}
	}
	for id := range known {
		if !seen[id] {
			t.Errorf("%s is pinned as a known difference but is no case", id)
		}
	}
	t.Logf("%d cases in %d sets, %d known differences", cases, len(sets), len(known))
}

// difference is how Go's decision differs from Python's: the outcome when
// it differs, then each constraint that differs, with both sides.
func difference(g, p conformanceDecision) string {
	if g.Error != "" || p.Error != "" {
		return fmt.Sprintf("Go error %q, Python error %q", g.Error, p.Error)
	}
	if len(g.Steps) > 0 || len(p.Steps) > 0 {
		if len(g.Steps) != len(p.Steps) {
			return fmt.Sprintf("Go %d steps, Python %d", len(g.Steps), len(p.Steps))
		}
		out := ""
		for i := range g.Steps {
			if !sameDecision(g.Steps[i], p.Steps[i]) {
				out += fmt.Sprintf("step %d: %s", i, difference(g.Steps[i], p.Steps[i]))
			}
		}
		return out
	}
	out := ""
	if g.Outcome != p.Outcome {
		out += fmt.Sprintf("outcome Go %s, Python %s; ", g.Outcome, p.Outcome)
	}
	if len(g.Constraints) != len(p.Constraints) {
		return out + fmt.Sprintf("Go %d constraints, Python %d", len(g.Constraints), len(p.Constraints))
	}
	for i, gc := range g.Constraints {
		if pc := p.Constraints[i]; !sameConstraint(gc, pc) {
			out += fmt.Sprintf("%s Go %s/%s/%s %q, Python %s/%s/%s %q; ", gc.ID, gc.Result, gc.Method, gc.EvidenceDigest, gc.Reason, pc.Result, pc.Method, pc.EvidenceDigest, pc.Reason)
		}
	}
	return out
}
