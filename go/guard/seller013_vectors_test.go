package guard

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// seller013Vectors are capsule-engine's own vectors and decisions under
// asg/seller/0.1.3 at 08cd96d (the merge of #139): the statement vectors
// (go-vectors: the claim reader, the live check inputs with and without
// the deal's claims in their history, and the replay of the two-thread
// sale), and the S1, S2, S3 and #131 real-seller decisions rerun there.
var seller013Vectors = filepath.Join("testdata", "seller-013-vectors")

const seller013Commit = "08cd96d03b6b23e0a7b71169dd5c24ea0381ff04"

func seller013Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller013()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// Every vendored file is byte for byte what its record says, and every file
// is recorded once: go-vectors/ by its own MANIFEST.sha256 (the engine side's; shasum
// -a 256 -c), whose sha256 PROVENANCE.json records, and every other file by
// PROVENANCE.json; the generators and the reused corpora are the ones
// vendored beside the earlier vectors.
func TestSeller013VectorsAreVendoredAsRecorded(t *testing.T) {
	var prov struct {
		EngineCommit string `json:"engine_commit"`
		Generators   map[string]string
		Corpora      struct {
			Files map[string]string `json:"files"`
		} `json:"corpora_reused"`
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, seller013Vectors, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	if prov.EngineCommit != seller013Commit {
		t.Fatalf("vectors at %s", prov.EngineCommit)
	}
	var onDisk, recorded []string
	err := fs.WalkDir(os.DirFS(seller013Vectors), ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || p == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		if got := fileSHA256Hex(readFile(t, seller013Vectors, name)); got != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, got, f.SHA256)
		}
	}
	if _, ok := prov.Files["go-vectors/MANIFEST.sha256"]; !ok {
		t.Error("provenance does not record go-vectors/MANIFEST.sha256")
	}
	sc := bufio.NewScanner(bytes.NewReader(readFile(t, seller013Vectors, "go-vectors", "MANIFEST.sha256")))
	for sc.Scan() {
		sum, name, ok := strings.Cut(sc.Text(), "  ")
		if !ok {
			t.Fatalf("MANIFEST line %q", sc.Text())
		}
		if got := fileSHA256Hex(readFile(t, seller013Vectors, "go-vectors", name)); got != sum {
			t.Errorf("go-vectors/%s sha256 %s, MANIFEST records %s", name, got, sum)
		}
		recorded = append(recorded, filepath.ToSlash(filepath.Join("go-vectors", name)))
	}
	slices.Sort(onDisk)
	if slices.Sort(recorded); !slices.Equal(onDisk, recorded) || len(recorded) != 19 {
		t.Fatalf("vendored %v, recorded %v", onDisk, recorded)
	}
	for _, m := range []map[string]string{prov.Generators, prov.Corpora.Files} {
		for name, sum := range m {
			if got := fileSHA256Hex(readFile(t, "testdata", name)); got != sum {
				t.Errorf("%s sha256 %s, provenance records %s", name, got, sum)
			}
		}
	}
	if len(prov.Generators) != 3 || len(prov.Corpora.Files) != 5 {
		t.Errorf("%d generators, %d reused corpora", len(prov.Generators), len(prov.Corpora.Files))
	}
}

// statementVectors is go-vectors/expected.json.
type statementVectors struct {
	Pack struct {
		PackID           string `json:"pack_id"`
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Wicket struct {
		WicketID string `json:"wicket_id"`
		Digest   string `json:"digest"`
	} `json:"wicket"`
	StatementFold struct {
		FoldID string `json:"fold_id"`
		Digest string `json:"digest"`
	} `json:"statement_fold"`
	Reader []struct {
		Case       string          `json:"case"`
		Capsule    json.RawMessage `json:"capsule"`
		AgentInput json.RawMessage `json:"agent_input"`
		Statement  json.RawMessage `json:"statement"`
	} `json:"reader"`
	Live []struct {
		Input        string            `json:"input"`
		InputHistory string            `json:"history"`
		Outcome      string            `json:"outcome"`
		Rules        map[string]string `json:"rules"`
		S05          s05Vector         `json:"s05"`
	} `json:"live"`
	Replay []struct {
		ActionID string    `json:"action_id"`
		Outcome  string    `json:"outcome"`
		S05      s05Vector `json:"s05"`
	} `json:"replay"`
}

type s05Vector struct {
	Result   string          `json:"result"`
	Evidence json.RawMessage `json:"evidence"`
}

// requiredStatementRule is the pack's rule run by required_disclosure: the
// engine's s05 (the Go plugin's docs have called the same rule s07).
const requiredStatementRule = "s05-required-statement-made-first"

func loadStatementVectors(t *testing.T) statementVectors {
	t.Helper()
	v := decodeJSON[statementVectors](t, readFile(t, seller013Vectors, "go-vectors", "expected.json"))
	if v.Pack.PackID != "asg/seller/0.1.3" || v.Pack.DefinitionDigest != "f0068688317b0cae3c40e839d7cff0137991aca466a878f9b558356f1808321a" ||
		v.Wicket.WicketID != "required_disclosure/1.1.0" || v.Wicket.Digest != "80f3af6e428f2331f6ab458997d996e172b9fc2e721b55fcf473f4e31d1492c0" ||
		v.StatementFold.FoldID != "disclosure.statement_seen/1.0.0" || v.StatementFold.Digest != "925e7ccd2e29b3f1984f966e968bdef47d0d22d2b85b8e0205a39d6a5a7ff0be" {
		t.Fatalf("vectors name %+v %+v %+v", v.Pack, v.Wicket, v.StatementFold)
	}
	if len(v.Reader) != 6 || len(v.Live) != 10 || len(v.Replay) != 5 {
		t.Fatalf("%d reader, %d live, %d replay vectors", len(v.Reader), len(v.Live), len(v.Replay))
	}
	return v
}

// jsonDigest is the JSON digest of raw, as the engine digests evidence.
func jsonDigest(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	v, err := canon.FromJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	d, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

// statementJSON is a statement record as the engine writes it
// (make_statement_record), for comparison with a vector's.
func statementJSON(t *testing.T, r Record) json.RawMessage {
	t.Helper()
	var at any
	if r.Timestamp != "" {
		at = r.Timestamp
	}
	b, err := json.Marshal(map[string]any{"capsule_id": r.CapsuleID, "operator": r.Operator, "timestamp": at,
		"asg_payload": map[string]any{"stated": map[string]string{"class": r.Stated.Class,
			"source_kind": r.Stated.SourceKind, "deal_id": r.Stated.DealID, "claim": r.Stated.Claim}}})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// statementsRedatedAt142 are the reader cases capsule-engine #142 (f91eb140,
// in b455063a) re-dated: a statement is dated by the claim's sealed
// x-deal-v0.at, never the capsule's timestamp, and these synthetic claims
// seal no at, so their timestamp is null and their capsule id changes. Each
// is the engine's statement_record at b455063a on the same case (rerun in a
// scratch clone); the 08cd96d vectors predate it.
var statementsRedatedAt142 = map[string]string{
	"agent condition":        "0650f44ef2d7413de877664a2391e9b9a7f958cccc599dcda8af16e13f6da377",
	"delivery_promise alias": "da2f1c89b7807cc667f27e0759833e49c08e68e22bcccbdd15b2213092e253f6",
}

// CROSS-IMPLEMENTATION: the claim reader (replay.py statement_record) on the
// engine's six cases: an agent's condition claim and a delivery_promise
// (read as delivery_date) are statements, with the engine's record and
// capsule id (as re-dated at b455063a: statementsRedatedAt142); a buyer's
// claim, a claim with no class, an agent's message, and a disclosure's
// personal-data fields are none.
func TestStatementReaderAgreesWithTheEngine(t *testing.T) {
	v := loadStatementVectors(t)
	statements := 0
	for _, c := range v.Reader {
		got, ok := StatementOf(c.Capsule, c.AgentInput)
		if string(c.Statement) == "null" {
			if ok {
				t.Errorf("%s: Go reads a statement %+v, the engine none", c.Case, got.Stated)
			}
			continue
		}
		statements++
		if !ok {
			t.Errorf("%s: Go reads no statement, the engine %s", c.Case, c.Statement)
			continue
		}
		if id, redated := statementsRedatedAt142[c.Case]; redated {
			if got.CapsuleID != id || got.Timestamp != "" {
				t.Errorf("%s: Go %s, the engine at b455063a %s with no timestamp", c.Case, statementJSON(t, got), id)
			}
			continue
		}
		if g, w := jsonDigest(t, statementJSON(t, got)), jsonDigest(t, c.Statement); g != w {
			t.Errorf("%s: Go %s, engine %s", c.Case, statementJSON(t, got), c.Statement)
		}
	}
	if statements != 2 {
		t.Errorf("%d reader cases are statements", statements)
	}
}

// liveHistories are the two ways expected.json gives each check input:
// with an accept disposition on every history act ("disposition-accept"),
// and with the deal's claims sealed by the check's time added to that
// history ("disposition-accept-claims-in-history", the vendored file).
const (
	historyAccepted   = "disposition-accept"
	historyWithClaims = "disposition-accept-claims-in-history"
)

// liveCheck is the check input name under history decided as check --emit
// decides it (DecodeCheckInput, then CheckWithHistory): the claims-in-history
// file as vendored, or, for historyAccepted, the same input with its claims
// taken out of the history.
func liveCheck(t *testing.T, e *Engine, name, history string) (Decision, InputHistory) {
	t.Helper()
	raw := readFile(t, seller013Vectors, "go-vectors", "check-inputs", name+".claims-in-history.json")
	if history == historyAccepted {
		var env map[string]json.RawMessage
		var entries []json.RawMessage
		if err := json.Unmarshal(raw, &env); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(env["history"], &entries); err != nil {
			t.Fatal(err)
		}
		acts := []json.RawMessage{}
		for _, entry := range entries {
			if !isClaimEntry(t, entry) {
				acts = append(acts, entry)
			}
		}
		b, err := json.Marshal(acts)
		if err != nil {
			t.Fatal(err)
		}
		env["history"] = b
		if raw, err = json.Marshal(env); err != nil {
			t.Fatal(err)
		}
	}
	a, h, err := DecodeCheckInput(raw)
	if err != nil {
		t.Fatalf("%s %s: %v", name, history, err)
	}
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatalf("%s %s: %v", name, history, err)
	}
	return d, h
}

// isClaimEntry is whether a history entry's agent_input is an x-deal-v0
// claim: what the engine's test added to the history.
func isClaimEntry(t *testing.T, entry json.RawMessage) bool {
	t.Helper()
	var e struct {
		AgentInput struct {
			XDeal struct {
				RecordType string `json:"record_type"`
			} `json:"x-deal-v0"`
		} `json:"agent_input"`
	}
	if err := json.Unmarshal(entry, &e); err != nil {
		t.Fatal(err)
	}
	return e.AgentInput.XDeal.RecordType == claimRecordType
}

// rulesOf is every rule's result on d, by rule id, as expected.json gives it.
func rulesOf(d Decision, p pack.Pack) map[string]string {
	out := map[string]string{}
	for _, r := range p.Rules {
		if c, ok := d.Constraint(r.Check); ok {
			out[r.ID] = string(c.Result)
		}
	}
	return out
}

// s05Agrees reports how c, required_disclosure on d, differs from want.
func s05Agrees(t *testing.T, d Decision, want s05Vector) string {
	t.Helper()
	c, ok := d.Constraint("required_disclosure")
	if !ok {
		return "no required_disclosure"
	}
	digest, digestable, err := c.EvidenceDigest()
	if err != nil || !digestable {
		return "evidence not digestable"
	}
	if string(c.Result) != want.Result || digest != jsonDigest(t, want.Evidence) {
		return "Go " + string(c.Result) + " " + string(canonJSON(t, c.evidence)) + ", engine " + want.Result + " " + string(want.Evidence)
	}
	return ""
}

func canonJSON(t *testing.T, v canon.Value) []byte {
	t.Helper()
	s, err := canon.JCS(v)
	if err != nil {
		t.Fatal(err)
	}
	return []byte(s)
}

// differ adds "<at> <field> Go <got> engine <want>" to diffs when they differ.
func differ(diffs *[]string, at, field, got, want string) {
	if got != want {
		*diffs = append(*diffs, at+" "+field+" Go "+got+" engine "+want)
	}
}

// floorUnreadVerdict is the engine's verdict as this plugin labels it: a
// would-allow decided while a floor in force was not read (price_floor's
// n/a naming commercial_bounds_opening, which the report reads as not
// evaluated, 7855d1f) is not_evaluable here, where the engine allows. A
// replay meets it on every check citing a task authority that commits to a
// floor, since a bundle never carries the floor's opening.
func floorUnreadVerdict(engine string, d Decision) string {
	if c, ok := d.Constraint("price_floor"); ok && c.unevaluated && engine == Allow {
		return VerdictNotEvaluable
	}
	return engine
}

// liveDifferences are where Go's decisions on the live rows differ from the
// engine's outside s05, which must agree exactly.
func liveDifferences(t *testing.T, v statementVectors) []string {
	t.Helper()
	e, p := seller013Engine(t)
	var diffs []string
	passes := 0
	for _, want := range v.Live {
		d, _ := liveCheck(t, e, want.Input, want.InputHistory)
		at := want.Input + " " + want.InputHistory
		if why := s05Agrees(t, d, want.S05); why != "" {
			t.Errorf("%s: s05 %s", at, why)
		}
		if want.S05.Result == string(Pass) {
			passes++
		}
		differ(&diffs, at, "outcome", d.Outcome, want.Outcome)
		got := rulesOf(d, p)
		if len(got) != len(want.Rules) {
			t.Errorf("%s: %d rules, the engine's %d", at, len(got), len(want.Rules))
		}
		for _, rule := range slices.Sorted(maps.Keys(want.Rules)) {
			differ(&diffs, at, rule, got[rule], want.Rules[rule])
		}
	}
	if passes != 5 {
		t.Errorf("s05 passes on %d live rows, the engine's on 5", passes)
	}
	return diffs
}

// CROSS-IMPLEMENTATION, live: each of the five check inputs of the
// two-thread sale (capsulectl 65f54e528a78), decided as check --emit
// decides it, with its history as the engine's test gives it. Without the
// deal's claims, s05 fails on all five (the statement is missing); with
// them, it passes on all five, counting only the checked deal's claim
// (a-commit-again holds both threads' claims, and counts one). s05's result
// and evidence, the outcome and every other rule's result are the engine's
// on every row, with no difference (since #136 and #137 are mirrored: s06
// reads record.proposal_at, s11 the history's acceptances).
//
// CROSS-IMPLEMENTATION, replay: the sale's two bundles replayed in order.
// Each offer and commit after its thread's claim passes s05 with the
// engine's evidence (the deal's statement counted once, the target's
// accepted statements none); its outcome is the engine's (a replayed
// commitment names no item, so single_commitment refuses it, #137).
func TestSeller013StatementVectorsAgreeWithTheEngine(t *testing.T) {
	v := loadStatementVectors(t)
	diffs := liveDifferences(t, v)
	e, _ := seller013Engine(t)
	outcomes := map[string]string{}
	for _, want := range v.Replay {
		outcomes[want.ActionID] = want.Outcome
	}
	decided := replayStatements(t, e, outcomes)
	for _, want := range v.Replay {
		d, ok := decided[want.ActionID]
		if !ok {
			t.Errorf("%s: no decision", want.ActionID)
			continue
		}
		if why := s05Agrees(t, d, want.S05); why != "" {
			t.Errorf("replay %s: s05 %s", want.ActionID, why)
		}
		differ(&diffs, "replay "+want.ActionID, "outcome", d.Outcome, want.Outcome)
	}
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// Every live row Go decides under asg/seller/0.1.3 as under the frozen
// asg/seller/0.1.2, s05 aside: the outcome is the same unless s05's result
// changed, and every other rule's result is the same. So the port changes
// s05 alone, and each pinned difference holds under 0.1.2 too. With a claim
// in the deal before the check, s05 fails under 0.1.2 (required_disclosure/
// 1.0.0 reads no statement) and passes under 0.1.3.
func TestSeller013ChangesOnlyS05(t *testing.T) {
	v := loadStatementVectors(t)
	e13, p := seller013Engine(t)
	e12, _ := seller012Engine(t)
	moved := 0
	for _, want := range v.Live {
		at := want.Input + " " + want.InputHistory
		d13, _ := liveCheck(t, e13, want.Input, want.InputHistory)
		d12, _ := liveCheck(t, e12, want.Input, want.InputHistory)
		r13, r12 := rulesOf(d13, p), rulesOf(d12, p)
		if r12[requiredStatementRule] != string(Fail) {
			t.Errorf("%s: s05 under 0.1.2 is %s", at, r12[requiredStatementRule])
		}
		if r13[requiredStatementRule] != r12[requiredStatementRule] {
			moved++
			if want.InputHistory != historyWithClaims {
				t.Errorf("%s: s05 moved with no claim in the history", at)
			}
		} else if d13.Outcome != d12.Outcome {
			t.Errorf("%s: outcome %s under 0.1.3, %s under 0.1.2, s05 unchanged", at, d13.Outcome, d12.Outcome)
		}
		delete(r13, requiredStatementRule)
		delete(r12, requiredStatementRule)
		if !maps.Equal(r13, r12) {
			t.Errorf("%s: rules under 0.1.3 %v, under 0.1.2 %v", at, r13, r12)
		}
	}
	if moved != 5 {
		t.Errorf("s05 moved on %d rows, the five with claims expected", moved)
	}
}

// replayStatements replays the live-history sale's two bundles in order, as
// the engine's replay reads them: a claim the agent made writes its
// statement record to the ledger when it is reached (once, however often
// the bundles give it); a record stating no act gets no decision; every
// other record is decided on the ledger so far and written to it as a dry
// run, with the engine's outcome where the vectors give one.
func replayStatements(t *testing.T, e *Engine, outcomes map[string]string) map[string]Decision {
	t.Helper()
	decided := map[string]Decision{}
	var ledger []Record
	written := map[string]bool{}
	for _, name := range []string{"buyer-a.bundle.json", "buyer-b.bundle.json"} {
		raw := readFile(t, seller013Vectors, "live-history", name)
		var bundle struct {
			Records     []json.RawMessage `json:"records"`
			Disclosures map[string]struct {
				AgentInput json.RawMessage `json:"agent_input"`
			} `json:"disclosures"`
		}
		if err := json.Unmarshal(raw, &bundle); err != nil {
			t.Fatal(err)
		}
		for _, capsule := range bundle.Records {
			var id struct {
				CapsuleID string `json:"capsule_id"`
			}
			if err := json.Unmarshal(capsule, &id); err != nil {
				t.Fatal(err)
			}
			shown, ok := bundle.Disclosures[id.CapsuleID]
			if !ok {
				continue
			}
			if stated, ok := StatementOf(capsule, shown.AgentInput); ok {
				if !written[stated.CapsuleID] {
					written[stated.CapsuleID] = true
					ledger = append(ledger, stated)
				}
				continue
			}
			var typed struct {
				Type string `json:"type"`
			}
			if err := json.Unmarshal(shown.AgentInput, &typed); err != nil {
				t.Fatal(err)
			}
			if typed.Type == "deal_report" {
				continue
			}
			a, err := DecodeRecord(raw, id.CapsuleID)
			if err != nil {
				t.Fatalf("%s %s: %v", name, id.CapsuleID, err)
			}
			if a.statesNoAct {
				continue
			}
			d, err := e.Check(a, MemoryHistory(ledger))
			if err != nil {
				t.Fatalf("%s %s: %v", name, a.ActionID, err)
			}
			decided[a.ActionID] = d
			outcome := d.Outcome
			if o, ok := outcomes[a.ActionID]; ok {
				outcome = o
			}
			rec := RecordFor(a, Decision{Outcome: outcome})
			rec.CapsuleID = id.CapsuleID
			rec.DryRun = ptr(true)
			ledger = append(ledger, rec)
		}
	}
	return decided
}

// CROSS-IMPLEMENTATION: S1 under asg/seller/0.1.3, rerun by the engine at
// 08cd96d. #139 changes no S1 result or outcome: only required_disclosure's
// evidence, now 1.1.0's (both folds, both counts, the deal in the scope),
// whose digest Go gives on every step, with no difference.
func TestSeller013S1ChecksAgreeWithTheEngine(t *testing.T) {
	e, p := seller013Engine(t)
	covered := map[string][]Result{"caps": {NotApplicable}, "offer_expiry": {Pass, Fail, NotApplicable},
		"price_floor": {Pass, Fail, NotApplicable}, "single_commitment": {Pass, Fail, NotApplicable}}
	diffs := runS1Vectors(t, e, p, filepath.Join(seller013Vectors, "s1"), seller013Commit, covered, "required_disclosure")
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// CROSS-IMPLEMENTATION: the S2 corpora under asg/seller/0.1.3 at 08cd96d,
// each check (required_disclosure now 1.1.0, with its evidence digest) and
// every other result and outcome as the engine's, with no difference.
func TestSeller013S2AgreesWithTheEngine(t *testing.T) {
	e, p := seller013Engine(t)
	dir := filepath.Join(seller013Vectors, "s2")
	for name, check := range map[string]string{"rail": "destination_rail", "promise-never": "promise_never",
		"promise-requires-approval": "promise_requires_approval", "required-disclosure": "required_disclosure"} {
		reports := runS2VectorsOn(t, e, p, dir, seller013Commit, name, check)
		noEchoInCorpus(t, name, reports)
	}
}

// CROSS-IMPLEMENTATION: the 0.1.1 acceptance scenario under asg/seller/0.1.3
// at 08cd96d, held as under 0.1.1 and 0.1.2, with no difference.
func TestSeller013ReleaseAgreesWithTheEngine(t *testing.T) {
	e, p := seller013Engine(t)
	runReleaseVectors(t, e, p, filepath.Join(seller013Vectors, "s3", "python-decisions.json"), seller013Commit)
}

// realSellerDifferences are the fields where got differs from want, one
// line each ("#<n> <source> <field> Go <x> engine <y>").
func realSellerDifferences(t *testing.T, got, want []realSellerDecision) []string {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%d decisions, the engine made %d", len(got), len(want))
	}
	var diffs []string
	for i := range want {
		g, w := map[string]json.RawMessage{}, map[string]json.RawMessage{}
		if err := json.Unmarshal([]byte(got[i].show()), &g); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal([]byte(want[i].show()), &w); err != nil {
			t.Fatal(err)
		}
		at := fmt.Sprintf("#%d %s", i+1, want[i].Source)
		for _, k := range slices.Sorted(maps.Keys(w)) {
			if k != "rules" {
				differ(&diffs, at, k, string(g[k]), string(w[k]))
			}
		}
		for _, rule := range slices.Sorted(maps.Keys(want[i].Rules)) {
			differ(&diffs, at, rule, got[i].Rules[rule], want[i].Rules[rule])
		}
	}
	return diffs
}

// CROSS-IMPLEMENTATION on REAL records under asg/seller/0.1.3: the #131
// real-seller fixture (capsulectl 831afeeb) replayed and checked live as
// TestRealSellerRecordsAgreeWithTheEngine does, held to the engine's own
// decisions at 08cd96d. What #139 changes there, Go changes too: the
// replay's commits after their thread's claim pass s05 (they were n/a,
// naming no target), and the live commits, whose check inputs carry no
// claim, fail it. Go gives every decision the engine's, with no difference.
func TestSeller013RealSellerAgreesWithTheEngine(t *testing.T) {
	want := decodeJSON[realSellerExpected](t, readFile(t, seller013Vectors, "real-seller", "expected_decisions.json"))
	if want.Pack.PackID != "asg/seller/0.1.3" || want.Pack.DefinitionDigest != "f0068688317b0cae3c40e839d7cff0137991aca466a878f9b558356f1808321a" {
		t.Fatalf("expectations under %s %s", want.Pack.PackID, want.Pack.DefinitionDigest)
	}
	e, p := seller013Engine(t)
	diffs := realSellerDifferences(t, realSellerRun(t, e, p, want), want.Decisions)
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}
