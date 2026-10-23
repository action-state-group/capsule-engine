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
	// CapsuleID is the capsule the Python engine sealed for an
	// action-ledger step (the caller seals in Go, so the runner gives the
	// record it appends this id), or a sale-bundle step's checked record.
	CapsuleID string `json:"capsule_id,omitempty"`
	// SealedCapsuleID is the decision capsule the engine's replay sealed for
	// a sale-bundle step.
	SealedCapsuleID string `json:"sealed_capsule_id,omitempty"`
	// Sale is a sale-bundle case's sale.
	Sale *conformanceSale `json:"sale,omitempty"`
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
	case "sale-bundle":
		var in struct {
			SaleBundle string `json:"sale_bundle"`
			Verify     string `json:"verify"`
		}
		if err := json.Unmarshal(raw, &in); err != nil {
			t.Fatalf("%s: %v", c.Case, err)
		}
		bundlePath := filepath.Join(conformanceRoot, filepath.FromSlash(in.SaleBundle))
		verifyPath := filepath.Join(conformanceRoot, filepath.FromSlash(in.Verify))
		if !strings.HasPrefix(verifyPath, filepath.Join(filepath.Dir(bundlePath), "verify")+string(filepath.Separator)) &&
			!strings.HasPrefix(verifyPath, filepath.Join(filepath.Dir(filepath.Dir(bundlePath)), "verify")+string(filepath.Separator)) {
			t.Fatalf("%s: the verify report is not under its fixture's verify/", c.Case)
		}
		bundle, err := os.ReadFile(bundlePath)
		if err != nil {
			t.Fatal(err)
		}
		verify, err := os.ReadFile(verifyPath)
		if err != nil {
			t.Fatal(err)
		}
		sale, err := ReadSaleBundle(bundle, verifiedBy(t, verify, bundle))
		if err != nil {
			return conformanceDecision{Error: "decode: " + err.Error()}
		}
		out := conformanceDecision{Sale: &conformanceSale{Complete: sale.Complete(), Findings: sale.Findings}}
		if out.Sale.Findings == nil {
			out.Sale.Findings = []string{}
		}
		// The engine's replay seals a decision of its own for each check, and
		// a later check's evidence names it; the caller seals in Go, so the
		// replay here gives each decided record that capsule's id.
		sealed := map[string]string{}
		for _, step := range c.Expected.Steps {
			if step.SealedCapsuleID != "" {
				sealed[step.CapsuleID] = step.SealedCapsuleID
			}
		}
		// Which checks are decided, and in what order, is this port's own:
		// the replay walks the records in seal order with the replay ledger's
		// own skips, so a check one side decides and the other does not is a
		// difference in the step list.
		for _, step := range replaySteps(t, e, sale, sealed) {
			got := step.decision
			got.CapsuleID, got.SealedCapsuleID = step.id, sealed[step.id]
			out.Steps = append(out.Steps, got)
		}
		return out
	}
	t.Fatalf("%s: unknown kind %q", c.Case, c.Kind)
	return conformanceDecision{}
}

type replayStep struct {
	id       string
	decision conformanceDecision
}

// replaySteps decides every check of sale in seal order, as the replay
// ledger builds it (replayLedgerSealed): statements and undisclosed records
// enter the ledger or are passed over, a record that states no act or cannot
// be evaluated is not decided, and each decided record enters the ledger
// under the capsule id sealed names for it.
func replaySteps(t *testing.T, e *Engine, s SaleBundle, sealed map[string]string) []replayStep {
	t.Helper()
	var steps []replayStep
	var ledger []Record
	claims := map[string]bool{}
	for _, x := range sealOrder(t, s) {
		id, _ := textOf(x.record["capsule_id"])
		shown, ok := x.thread.disclosed[id]
		if !ok {
			continue
		}
		capsule, err := json.Marshal(x.record)
		if err != nil {
			t.Fatal(err)
		}
		if stated, ok := StatementOf(capsule, shown); ok {
			if !claims[stated.Stated.Claim] {
				claims[stated.Stated.Claim] = true
				ledger = append(ledger, stated)
			}
			continue
		}
		a, h, err := s.Check(id)
		if err != nil || a.statesNoAct || len(a.Unevaluable()) > 0 {
			continue
		}
		d, err := e.CheckSale(a, ledger, h)
		if err != nil {
			steps = append(steps, replayStep{id, conformanceDecision{Error: "check: " + err.Error()}})
			continue
		}
		steps = append(steps, replayStep{id, decisionShape(t, id, d)})
		rec := RecordFor(a, d)
		rec.CapsuleID, rec.DryRun = id, ptr(true)
		if as, ok := sealed[id]; ok {
			rec.CapsuleID = as
		}
		ledger = append(ledger, rec)
	}
	return steps
}

// conformanceSale is a sale-bundle case's sale: whether the replay holds
// every thread, and each link that does not hold.
type conformanceSale struct {
	Complete bool     `json:"complete"`
	Findings []string `json:"findings"`
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
	if (a.Sale == nil) != (b.Sale == nil) || a.Sale != nil && (a.Sale.Complete != b.Sale.Complete || !slices.Equal(a.Sale.Findings, b.Sale.Findings)) {
		return false
	}
	if len(a.Steps) != len(b.Steps) {
		return false
	}
	for i := range a.Steps {
		if a.Steps[i].CapsuleID != b.Steps[i].CapsuleID || !agrees(a.Steps[i], b.Steps[i], exact) {
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
				// A sale's pin covers its verification only: every step
				// still agrees, so a re-pin cannot accept a step divergence.
				if c.Kind == "sale-bundle" && !sameDecision(conformanceDecision{Steps: got.Steps}, conformanceDecision{Steps: c.Expected.Steps}) {
					t.Errorf("%s: a sale's steps are not pinned: %s", id, difference(conformanceDecision{Steps: got.Steps}, conformanceDecision{Steps: c.Expected.Steps}))
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
	if g.Sale != nil && p.Sale != nil && (g.Sale.Complete != p.Sale.Complete || !slices.Equal(g.Sale.Findings, p.Sale.Findings)) {
		return fmt.Sprintf("sale Go %+v, Python %+v", *g.Sale, *p.Sale)
	}
	if len(g.Steps) > 0 || len(p.Steps) > 0 {
		if len(g.Steps) != len(p.Steps) {
			return fmt.Sprintf("Go %d steps, Python %d", len(g.Steps), len(p.Steps))
		}
		out := ""
		for i := range g.Steps {
			if g.Steps[i].CapsuleID != p.Steps[i].CapsuleID {
				out += fmt.Sprintf("step %d: Go decides %s, Python %s; ", i, g.Steps[i].CapsuleID, p.Steps[i].CapsuleID)
				continue
			}
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
