package guard

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// platformRoleVectors are capsule-engine's decisions at 7339028 (#173) on
// disclosures to a platform recipient and its neighbours, under the
// everyday and seller catalog packs, written by generate.py beside them.
var platformRoleVectors = filepath.Join("testdata", "platform-role-vectors")

const platformRoleCommit = "7339028ab2117d6c7adec89a26cd5022b8ac7140"

func seller014Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller014()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

type platformConstraint struct {
	ID             string  `json:"id"`
	Result         string  `json:"result"`
	Reason         string  `json:"reason"`
	EvidenceDigest *string `json:"evidence_digest"`
}

type platformDecision struct {
	Scenario         string               `json:"scenario"`
	Action           json.RawMessage      `json:"action"`
	Record           json.RawMessage      `json:"record"`
	DisclosedClasses []string             `json:"disclosed_classes"`
	Outcome          string               `json:"outcome"`
	Constraints      []platformConstraint `json:"constraints"`
	Obligations      []parityObligation   `json:"obligations"`
}

type platformPack struct {
	PackID           string             `json:"pack_id"`
	DefinitionDigest string             `json:"definition_digest"`
	Scenarios        []platformDecision `json:"scenarios"`
	Records          []platformDecision `json:"records"`
}

// platformChecks are the checks #173 changed, held to the engine's result,
// evidence digest and own reason; the rules they measure, to its rule
// results.
var platformChecks = []string{"recipient_role", "action_class_gate", "release_on_acceptance"}

// CROSS-IMPLEMENTATION: every scenario of capsule-engine #173's platform
// recipient, as an action and as a capsulectl deal check record whose body's
// disclosing the engine bridges, under asg/everyday/0.3.7 and
// asg/seller/0.1.4, with NO known difference: the outcome, every rule's
// result, and for recipient_role, action_class_gate and
// release_on_acceptance the result, the evidence digest and the reason the
// engine gives (a failure's, and an n/a's where the check names its own),
// which the plugin reports as the rule's reason. On a record, the classes
// Go reads from disclosing are the engine's.
func TestPlatformRoleVectorsAgreeWithTheEngine(t *testing.T) {
	var v struct {
		Commit string                  `json:"capsule_engine_commit"`
		Packs  map[string]platformPack `json:"packs"`
	}
	if err := json.Unmarshal(readFile(t, platformRoleVectors, "engine-results.json"), &v); err != nil {
		t.Fatal(err)
	}
	if v.Commit != platformRoleCommit || len(v.Packs) != 2 {
		t.Fatalf("vectors at %s for %d packs", v.Commit, len(v.Packs))
	}
	engines := map[string]func(*testing.T) (*Engine, pack.Pack){"everyday": everyday037Engine, "seller": seller014Engine}
	n := 0
	for name, want := range v.Packs {
		e, p := engines[name](t)
		if want.PackID != p.ID || want.DefinitionDigest != p.DefinitionDigest {
			t.Fatalf("%s: vectors under %s %s, pack %s %s", name, want.PackID, want.DefinitionDigest, p.ID, p.DefinitionDigest)
		}
		for _, s := range want.Scenarios {
			a, err := DecodeAction(bytes.NewReader(s.Action))
			if err != nil {
				t.Fatalf("%s %s: %v", name, s.Scenario, err)
			}
			comparePlatformDecision(t, name, e, p, a, s)
			n++
		}
		for _, s := range want.Records {
			a, err := DecodeRecord(s.Record, "")
			if err != nil {
				t.Fatalf("%s %s: %v", name, s.Scenario, err)
			}
			if !slices.Equal(a.DisclosedClasses, s.DisclosedClasses) {
				t.Errorf("%s %s: disclosed_classes Go %v, engine %v", name, s.Scenario, a.DisclosedClasses, s.DisclosedClasses)
			}
			if slices.Contains(a.extra, "disclosing") {
				t.Errorf("%s %s: disclosing listed as ignored", name, s.Scenario)
			}
			comparePlatformDecision(t, name, e, p, a, s)
			n++
		}
	}
	if n != 52 {
		t.Errorf("%d decisions compared, want 52", n)
	}
}

func comparePlatformDecision(t *testing.T, packName string, e *Engine, p pack.Pack, a Action, s platformDecision) {
	t.Helper()
	at := packName + " " + s.Scenario
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatalf("%s: %v", at, err)
	}
	if d.Outcome != s.Outcome {
		t.Errorf("%s: outcome Go %s, engine %s", at, d.Outcome, s.Outcome)
	}
	compared := 0
	for _, pc := range s.Constraints {
		if !slices.Contains(platformChecks, pc.ID) {
			continue
		}
		compared++
		c, ok := d.Constraint(pc.ID)
		if !ok {
			t.Errorf("%s: no %s", at, pc.ID)
			continue
		}
		digest, _, err := c.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		if string(c.Result) != pc.Result || pc.EvidenceDigest == nil || digest != *pc.EvidenceDigest {
			t.Errorf("%s: %s Go %s %s, engine %s %v", at, pc.ID, c.Result, digest, pc.Result, pc.EvidenceDigest)
		}
		// The reason the check gives of its own: a failure's, and an
		// out-of-scope n/a's where it names one; the engine's otherwise is
		// the generic reason the report words for itself. A gate failure
		// is worded by the report per selector (selectorResult), as before.
		if c.Result == Fail && pc.ID == "action_class_gate" {
			continue
		}
		own := c.failNote
		if c.Result == NotApplicable {
			own = c.scopeNote
		}
		names := c.Result == Fail || strings.Contains(pc.Reason, "leave out recipient_role") || strings.Contains(pc.Reason, "not a buyer")
		if names && own != pc.Reason {
			t.Errorf("%s: %s reason Go %q, engine %q", at, pc.ID, own, pc.Reason)
		}
		if !names && own != "" {
			t.Errorf("%s: %s Go gives its own reason %q, engine %q", at, pc.ID, own, pc.Reason)
		}
	}
	if compared == 0 {
		t.Errorf("%s: no platform check among the engine's constraints", at)
	}
	rules := map[string]RuleResult{}
	for _, r := range e.Report(a, d).Rules {
		rules[r.Rule] = r
	}
	verdict := map[string]string{"pass": VerdictPass, "fail": VerdictFail, "n/a": VerdictNotApplicable}
	for _, o := range s.Obligations {
		r, ok := rules[o.ID]
		if !ok {
			t.Errorf("%s: no rule %s in the report", at, o.ID)
			continue
		}
		if r.Verdict != verdict[o.Result] {
			t.Errorf("%s: %s Go %s, engine %s", at, o.ID, r.Verdict, o.Result)
		}
	}
	// A rule whose check the engine leaves out for a platform (s13) reports
	// the engine's reason word for word. A gate rule (r11, r12) says the
	// generic sentence: the engine's names an action class (#182).
	for _, pc := range s.Constraints {
		if pc.Result != "n/a" || !(strings.Contains(pc.Reason, "leave out recipient_role") || strings.Contains(pc.Reason, "not a buyer")) {
			continue
		}
		for _, rule := range p.Rules {
			want := pc.Reason
			if pc.ID == "action_class_gate" {
				want = gateNotApplicable
			}
			if rule.Check == pc.ID && rules[rule.ID].Reason != want {
				t.Errorf("%s: %s reason %q, want %q", at, rule.ID, rules[rule.ID].Reason, want)
			}
		}
	}
	// The rule recipient_role measures reports the engine's reason word
	// for word when it fails.
	for _, pc := range s.Constraints {
		if pc.ID != "recipient_role" || pc.Result != "fail" {
			continue
		}
		for _, rule := range p.Rules {
			if rule.Check == "recipient_role" && rules[rule.ID].Reason != pc.Reason {
				t.Errorf("%s: %s reason %q, engine %q", at, rule.ID, rules[rule.ID].Reason, pc.Reason)
			}
		}
	}
}

// The #131 real-seller fixture under asg/seller/0.1.4, held to the engine's
// decisions at 7339028 (tests/fixtures/real-seller/expected_decisions.json
// there, whose only change from 0.1.3's is its pack header): its sale shares
// nothing with a platform, so every decision is 0.1.3's.
func TestSeller014RealSellerAgreesWithTheEngine(t *testing.T) {
	want := decodeJSON[realSellerExpected](t, readFile(t, "testdata", "seller-014-vectors", "real-seller", "expected_decisions.json"))
	e, p := seller014Engine(t)
	if want.Pack.PackID != p.ID || want.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("expectations under %s %s", want.Pack.PackID, want.Pack.DefinitionDigest)
	}
	diffs := realSellerDifferences(t, realSellerRun(t, e, p, want), want.Decisions)
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// disclosed_classes on the action is held as the engine's Action holds it:
// a non-empty list of known class names, sorted and each named once.
func TestDisclosedClassesOnAnActionAreTheEnginesShape(t *testing.T) {
	base := `{"verb":"share","operator":"op","developer":"dev","timestamp":"2026-10-21T10:00:00Z","disclosed_classes":%s}`
	for _, ok := range []string{`["email"]`, `["email","phone"]`, `null`} {
		if _, err := DecodeAction(strings.NewReader(strings.Replace(base, "%s", ok, 1))); err != nil {
			t.Errorf("%s refused: %v", ok, err)
		}
	}
	for _, bad := range []string{`[]`, `["phone","email"]`, `["email","email"]`, `["ssn"]`, `"email"`} {
		if _, err := DecodeAction(strings.NewReader(strings.Replace(base, "%s", bad, 1))); err == nil {
			t.Errorf("%s accepted", bad)
		}
	}
}

// pyRepr quotes as Python's repr does.
func TestPyRepr(t *testing.T) {
	for in, want := range map[string]string{"platform": `'platform'`, "o'brien": `"o'brien"`, `a'b"c`: `'a\'b"c'`,
		`back\slash`: `'back\\slash'`, "tab\there": `'tab\there'`, "é": `'é'`, "\x00": `'\x00'`} {
		if got := pyRepr(in); got != want {
			t.Errorf("pyRepr(%q) = %s, want %s", in, got, want)
		}
	}
}

// The platform vectors and the 0.1.4 real-seller decisions are byte for
// byte what their PROVENANCE.json records, and each records every file.
func TestPlatformVectorsAreVendoredAsRecorded(t *testing.T) {
	for _, dir := range []string{platformRoleVectors, filepath.Join("testdata", "seller-014-vectors")} {
		prov := decodeJSON[struct {
			Files map[string]struct {
				SHA256 string `json:"sha256"`
			} `json:"files"`
		}](t, readFile(t, dir, "PROVENANCE.json"))
		if len(prov.Files) == 0 {
			t.Fatalf("%s: no files recorded", dir)
		}
		for name, f := range prov.Files {
			if got := fileSHA256Hex(readFile(t, dir, name)); got != f.SHA256 {
				t.Errorf("%s/%s sha256 %s, provenance records %s", dir, name, got, f.SHA256)
			}
		}
	}
}

// role_classes is refused unless every role it names is in roles and not in
// allowed_roles, with at least one class (recipient_role.py raises).
func TestRoleClassesOutsideTheRolesAreRefused(t *testing.T) {
	base := roleConfig{ActionClasses: []string{"disclosure.personal"}, Roles: []string{"fulfilling_merchant", "platform", "self"},
		AllowedRoles: []string{"fulfilling_merchant", "self"}}
	ok := base
	ok.RoleClasses = map[string][]string{"platform": {"email"}}
	if err := checkRoleClasses(ok, "recipient_role/1.1.0"); err != nil {
		t.Fatal(err)
	}
	for name, rc := range map[string]map[string][]string{
		"not in roles":     {"courier": {"email"}},
		"in allowed_roles": {"self": {"email"}},
		"no class":         {"platform": {}},
	} {
		c := base
		c.RoleClasses = rc
		if err := checkRoleClasses(c, "recipient_role/1.1.0"); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

// except_recipient_roles is a non-empty list of names that narrows a
// selector and selects nothing on its own (action_class_gate.py
// parse_selectors).
func TestExceptRecipientRolesIsParsedAsTheEngineParsesIt(t *testing.T) {
	e, _ := everydayEngine(t)
	parse := func(src string) error {
		var n yaml.Node
		if err := yaml.Unmarshal([]byte(src), &n); err != nil {
			t.Fatal(err)
		}
		_, err := parseSelectors(*n.Content[0], e.tax)
		return err
	}
	if err := parse("s: {trigger_classes: [DISCLOSE], except_recipient_roles: [platform], on_match: fail}"); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{
		"s: {trigger_classes: [DISCLOSE], except_recipient_roles: [], on_match: fail}",
		"s: {trigger_classes: [DISCLOSE], except_recipient_roles: [1], on_match: fail}",
		"s: {trigger_classes: [DISCLOSE], except_recipient_roles: platform, on_match: fail}",
		"s: {except_recipient_roles: [platform], on_match: fail}",
	} {
		if err := parse(bad); err == nil {
			t.Errorf("%s accepted", bad)
		}
	}
}
