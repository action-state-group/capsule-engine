package guard

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// fixtureSet is one pack with the engine's own fixtures and Python parity
// run for it: the legs run over every set.
type fixtureSet struct {
	name, dir, parityDir string
	load                 func(t *testing.T) pack.Pack
}

var (
	// fixtures037 is asg/everyday/0.3.7 (capsule-engine #173, 7339028), the pack
	// the engine's fixtures and Python parity were run for. The default,
	// asg/everyday/0.3.8, is 0.3.7 with action_class_gate/1.2.0 on r11 and
	// r12 alone (TestEveryday038Is037WithToldBefore), which
	// TestActionClassGate12AgreesWithTheEngine holds to the engine, so these
	// vectors still hold every other check the default runs.
	fixtures037 = fixtureSet{"everyday-0.3.7", "testdata/everyday-fixtures", "testdata/parity", func(t *testing.T) pack.Pack {
		t.Helper()
		p, err := pack.LoadEveryday037()
		if err != nil {
			t.Fatal(err)
		}
		return p
	}}
	// fixtures038 is the default pack, asg/everyday/0.3.8, with the engine's
	// own fixtures and Python parity at the commit that cut it (capsule-engine
	// 0c93a58): testdata/everyday-fixtures-0.3.8 and testdata/parity-0.3.8.
	fixtures038 = fixtureSet{"everyday-0.3.8", "testdata/everyday-fixtures-0.3.8", "testdata/parity-0.3.8", func(t *testing.T) pack.Pack {
		t.Helper()
		p, err := pack.LoadEveryday038()
		if err != nil {
			t.Fatal(err)
		}
		return p
	}}
	fixtureSets = []fixtureSet{fixtures037, fixtures038}
)

func (fx fixtureSet) engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p := fx.load(t)
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// measuredChecks are the checks the pack's measured obligations cite.
func measuredChecks(p pack.Pack) []string {
	var out []string
	for _, r := range p.Rules {
		if r.Check != "" && !slices.Contains(out, r.Check) {
			out = append(out, r.Check)
		}
	}
	return out
}

type naDecl struct {
	Population   string `yaml:"population"`
	MissingField string `yaml:"missing_field"`
}

type tableRow struct {
	Scenario string            `yaml:"scenario"`
	ActionID string            `yaml:"action_id"`
	Note     string            `yaml:"note"`
	Checks   map[string]string `yaml:"checks"`
	NA       map[string]naDecl `yaml:"n_a"`
	Verdict  string            `yaml:"verdict"`
}

type goldRow struct {
	HandAuthored       bool   `yaml:"hand_authored"`
	ActionID           string `yaml:"action_id"`
	CapsuleID          string `yaml:"capsule_id"`
	EvaluatorTier      string `yaml:"evaluator_tier"`
	DeclaredPopulation string `yaml:"declared_population"`
	Record             *struct {
		ID        string `yaml:"id"`
		Result    string `yaml:"result"`
		Severity  string `yaml:"severity"`
		CheckType string `yaml:"check_type"`
		Method    string `yaml:"method"`
	} `yaml:"record"`
	ConfigIdentity struct {
		PackID     string            `yaml:"pack_id"`
		PackDigest string            `yaml:"pack_digest"`
		Wickets    map[string]string `yaml:"wickets"`
	} `yaml:"config_identity"`
	CodeIdentity struct {
		Kind, Value, Assurance string
	} `yaml:"code_identity"`
}

type parityConstraint struct {
	ID             string  `json:"id"`
	Result         string  `json:"result"`
	EvidenceDigest *string `json:"evidence_digest"`
}

type parityScenario struct {
	Scenario    string             `json:"scenario"`
	Action      json.RawMessage    `json:"action"`
	Outcome     string             `json:"outcome"`
	Expected    string             `json:"expected"`
	CapsuleID   string             `json:"capsule_id"`
	Constraints []parityConstraint `json:"constraints"`
	// Obligations are capsule-engine packs.obligation_results for the
	// decision (a5e9e30 on): each rule's own result, pass, fail or n/a.
	Obligations []parityObligation `json:"obligations"`
	// TaskAuthorityRecord is the sealed task-authority record supplied
	// beside the action, when the scenario supplies one.
	TaskAuthorityRecord json.RawMessage `json:"task_authority_record"`
}

type parityObligation struct {
	ID     string `json:"id"`
	Result string `json:"result"`
}

type parityFile struct {
	CapsuleEngineCommit string           `json:"capsule_engine_commit"`
	Scenarios           []parityScenario `json:"scenarios"`
}

func readFile(t *testing.T, parts ...string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(parts...))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func decodeYAML[T any](t *testing.T, b []byte) T {
	t.Helper()
	var out T
	dec := yaml.NewDecoder(bytes.NewReader(b))
	dec.KnownFields(true)
	if err := dec.Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
}

func decisionTable(t *testing.T, fx fixtureSet) []tableRow {
	t.Helper()
	return decodeYAML[struct {
		Rows []tableRow `yaml:"rows"`
	}](t, readFile(t, fx.dir, "decision_table.yaml")).Rows
}

func goldLabels(t *testing.T, fx fixtureSet) []goldRow {
	t.Helper()
	return decodeYAML[struct {
		Rows []goldRow `yaml:"rows"`
	}](t, readFile(t, fx.dir, "gold_labels.yaml")).Rows
}

func miniLedger(t *testing.T, fx fixtureSet) []Record {
	t.Helper()
	recs, err := readCapsules(bytes.NewReader(readFile(t, fx.dir, "mini_ledger.jsonl")))
	if err != nil {
		t.Fatal(err)
	}
	return recs
}

func parity(t *testing.T, fx fixtureSet) parityFile {
	t.Helper()
	var p parityFile
	if err := json.Unmarshal(readFile(t, fx.parityDir, "python-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	return p
}

func scenarioActions(t *testing.T, fx fixtureSet) []Action {
	t.Helper()
	var out []Action
	for _, s := range parity(t, fx).Scenarios {
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		out = append(out, a)
	}
	return out
}

// everydayEngine is an engine over the default pack, asg/everyday/0.3.8.
func everydayEngine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// everyday037Engine is an engine over the frozen asg/everyday/0.3.7, for the
// vectors the engine decided under it (fixtures037).
func everyday037Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	return fixtures037.engine(t)
}

// everyday036Engine is an engine over the frozen asg/everyday/0.3.6, for the
// vectors the engine decided under it. 0.3.7 cites the same definitions but
// recipient_role and action_class_gate (TestEveryday037Is036WithThePlatform),
// so those vectors, which disclose nothing to a platform, still hold the
// checks the shipped pack runs.
func everyday036Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadEveryday036()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// goReplay runs the scenarios in order through the Go engine alone: each
// decision is appended as the record the next scenario sees, starting from
// the fixture ledger's install record (the only non-decision record). Each
// record is marked a dry run, as the engine's replay (engine.check with
// dry_run=True) seals it.
func goReplay(t *testing.T, e *Engine, fx fixtureSet) map[string]Decision {
	t.Helper()
	ledger := miniLedger(t, fx)[:1]
	scenario := map[string]string{}
	for _, row := range decisionTable(t, fx) {
		scenario[row.ActionID] = row.Scenario
	}
	out := map[string]Decision{}
	py := parity(t, fx).Scenarios
	for i, a := range scenarioActions(t, fx) {
		d, err := e.CheckWithTaskAuthority(a, MemoryHistory(ledger), py[i].TaskAuthorityRecord)
		if err != nil {
			t.Fatalf("%s: %v", a.ActionID, err)
		}
		out[a.ActionID] = d
		rec := RecordFor(a, d)
		if !realRun[scenario[a.ActionID]] {
			rec.DryRun = ptr(true)
		}
		ledger = append(ledger, rec)
	}
	return out
}

func TestVendoredFixturesUnchanged(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { vendoredFixturesUnchanged(t, fx) })
	}
}

func vendoredFixturesUnchanged(t *testing.T, fx fixtureSet) {
	var prov struct {
		SourceCommit string `json:"source_commit"`
		Files        map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, fx.dir, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	if len(prov.Files) != 3 {
		t.Fatalf("provenance names %d files, expected the 3 fixtures", len(prov.Files))
	}
	for name, f := range prov.Files {
		sum := sha256.Sum256(readFile(t, fx.dir, name))
		if got := hex.EncodeToString(sum[:]); got != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance %s", name, got, f.SHA256)
		}
	}
	if p := parity(t, fx); p.CapsuleEngineCommit != prov.SourceCommit {
		t.Fatalf("parity run at %s, fixtures from %s", p.CapsuleEngineCommit, prov.SourceCommit)
	}
}

// notYetRun reports a constraint for a cited check this port does not run
// yet, in its scope: the engine's result for it is recorded, not compared.
func notYetRun(c Constraint) bool { return c.Pending() == NotYetRun }

// anyNotYetRun reports whether d has a not-yet-run constraint, which makes
// its consolidated decision not_evaluable rather than allow.
func anyNotYetRun(d Decision) bool {
	for _, c := range d.Constraints {
		if notYetRun(c) {
			return true
		}
	}
	return false
}

// unportedFailed reports whether the engine's results fail a check this
// port does not run yet.
func unportedFailed(e *Engine, results map[string]string) bool {
	for id, r := range results {
		if _, unported := e.unported[id]; unported && r == "fail" {
			return true
		}
	}
	return false
}

// realRun are the scenarios the engine's acceptance test records as real
// decisions rather than dry runs (capsule-engine 365eb92, unchanged at a5e9e30
// tests/test_pack_everyday_acceptance.py REAL_RUN): the earlier payments a
// later seen-before check finds. Every other scenario is a dry run, which
// the seen-before and spend folds leave out.
var realRun = map[string]bool{"merchant-history-real-payment": true, "recipient-history-real-payment": true}

// readCapsules reads a JSONL fixture, one Agent Action Capsule per line, in
// file order: the records a MemoryHistory holds for a test.
func readCapsules(r io.Reader) ([]Record, error) {
	var out []Record
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 1<<16), 1<<24)
	for n := 1; sc.Scan(); n++ {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		rec, err := DecodeCapsule(line)
		if err != nil {
			return nil, fmt.Errorf("line %d: %w", n, err)
		}
		out = append(out, rec)
	}
	return out, sc.Err()
}
