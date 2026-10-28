package guard

import (
	"encoding/json"
	"errors"
	"maps"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

func sellerEngine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller010()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// sellerUnported are the checks asg/seller/0.1.0 cites that this port does
// not run yet, with the rule each measures.
var sellerUnported = map[string]string{}

// The seller pack loads into the engine with exactly the unported gap named:
// the rules of sellerUnported not yet run, and the others run by ported
// checks (caps/1.0.0, which measures no rule, among them).
func TestSellerNotYetRunIsExactlyTheUnported(t *testing.T) {
	e, p := sellerEngine(t)
	if got := slices.Sorted(maps.Keys(e.unported)); !slices.Equal(got, slices.Sorted(maps.Keys(sellerUnported))) {
		t.Fatalf("unported checks %v, want %v", got, slices.Sorted(maps.Keys(sellerUnported)))
	}
	want := map[string]bool{}
	for _, rule := range sellerUnported {
		want[rule] = true
	}
	if got := NotYetRunRules(p); !maps.Equal(got, want) {
		t.Fatalf("not yet run %v, want %v", got, want)
	}
	for _, r := range p.Rules {
		if !want[r.ID] && !slices.Contains(implementedChecks, r.Check) {
			t.Errorf("%s cites %s, which is neither run nor named as not yet run", r.ID, r.Check)
		}
	}
}

// Every check asg/seller/0.1.0 cites is run: on an action of any class, no
// constraint is "not yet run by this plugin".
func TestSellerNoCheckIsNotYetRun(t *testing.T) {
	e, _ := sellerEngine(t)
	for _, class := range []string{"info.query", "marketplace.offer", "marketplace.sale", "communication.send", "money.transfer",
		"disclosure.personal", "agreement.accept", "external_commitment.other"} {
		a := Action{Verb: "act", Operator: "op", Developer: "dev", ActionClass: strp(class), ActionID: "act/" + class,
			ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", TaxonomyVersion: strp("6"), Target: strp("cp/a"), AmountMinor: i64p(100)}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		for _, c := range d.Constraints {
			if c.Pending() == NotYetRun {
				t.Errorf("%s on %s: not yet run", c.ID, class)
			}
		}
	}
}

// An unported rule out of its scope says which classes it covers, from its
// own definition.
func TestSellerUnportedOutOfScopeReason(t *testing.T) {
	e, _ := sellerEngine(t)
	a := Action{Verb: "read", Operator: "op", Developer: "dev", ActionClass: strp("info.query"), ActionID: "read/1",
		ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", TaxonomyVersion: strp("6")}
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		"s03-price-below-the-floor":        "covers agreement.accept, marketplace.offer, marketplace.sale only",
		"s07-payment-rail-the-user-allows": "covers marketplace.sale only",
		"s11-one-commitment-per-sale":      "covers agreement.accept, external_commitment.other, marketplace.offer, marketplace.sale only",
	}
	for _, r := range e.Report(a, d).Rules {
		if w, ok := want[r.Rule]; ok && (r.Verdict != VerdictNotApplicable || r.Reason != w) {
			t.Errorf("%s: %s %q, want not_applicable %q", r.Rule, r.Verdict, r.Reason, w)
		}
	}
}

// seller.recipient_role on its own: the seller's counterparty is the buyer.
// buyer and self pass; third_party fails; fulfilling_merchant, a buyer-side
// role outside the seller's closed set, fails as not recognised. (The
// engine's scenario fails it only on a step that cites a mandate, which this
// port refuses, so the vectors do not compare the fail.)
func TestSellerRecipientRole(t *testing.T) {
	e, _ := sellerEngine(t)
	for role, want := range map[string]Result{"buyer": Pass, "self": Pass, "third_party": Fail, "fulfilling_merchant": Fail} {
		a := Action{Verb: "share_address", Operator: "op", Developer: "dev", ActionClass: strp("disclosure.personal"), ActionID: "share/" + role,
			ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", TaxonomyVersion: strp("6"), Target: strp("cp/a"), RecipientRole: strp(role)}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		if c, _ := d.Constraint("recipient_role"); c.Result != want {
			t.Errorf("recipient_role %s: %s, want %s", role, c.Result, want)
		}
	}
}

// Rule s12 cites the engine's reference check: the report gives its result
// as s12's row and adds no separate reference row, decided or not.
func TestSellerReportNamesS12ForTheReferenceCheck(t *testing.T) {
	e, p := sellerEngine(t)
	a := Action{Verb: "read", Operator: "op", Developer: "dev", ActionClass: strp("info.query"), ActionID: "read/1",
		ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", TaxonomyVersion: strp("6")}
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatal(err)
	}
	unevaluable := a
	unevaluable.unevaluable = []string{"capsule header has no operator"}
	for name, rep := range map[string]CheckReport{"decided": e.Report(a, d), "unevaluable": e.ReportUnevaluable(unevaluable)} {
		var ids []string
		for _, r := range rep.Rules {
			ids = append(ids, r.Rule)
			if r.Rule == "s12-cited-mandate-verifies" && r.Check != "verify_before_dispatch/1.0.0" {
				t.Errorf("%s: s12 check %q", name, r.Check)
			}
		}
		var want []string
		for _, r := range p.Rules {
			want = append(want, r.ID)
		}
		if !slices.Equal(ids, want) {
			t.Errorf("%s: rows %v, want the pack's rules %v and no reference row", name, ids, want)
		}
		if rep.Pack.ID != "asg/seller/0.1.0" {
			t.Errorf("%s: pack %s", name, rep.Pack.ID)
		}
	}
}

// A seller definition is accepted only at its pinned digest: a citation of
// another version, or of the pinned id at another digest, is refused.
func TestEngineRefusesAnUnpinnedDefinition(t *testing.T) {
	_, p := sellerEngine(t)
	for name, edit := range map[string]func(*pack.Wicket){
		"another version": func(w *pack.Wicket) { w.ID = "price_floor/9.9.9" },
		"another digest":  func(w *pack.Wicket) { w.Digest = strings.Repeat("0", 64) },
	} {
		q := p
		q.Wickets = slices.Clone(p.Wickets)
		i := slices.IndexFunc(q.Wickets, func(w pack.Wicket) bool { return w.Check == "price_floor" })
		edit(&q.Wickets[i])
		if _, err := NewEngine(q); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	q := p
	q.Folds = slices.Clone(p.Folds)
	q.Folds[1].Digest = strings.Repeat("0", 64)
	if q.Folds[1].ID != "disclosure.class_seen/1.0.0" {
		t.Fatalf("folds[1] is %s", q.Folds[1].ID)
	}
	if _, err := NewEngine(q); err == nil {
		t.Error("disclosure.class_seen/1.0.0 at another digest was accepted")
	}
}

// sellerCorpus is capsule-engine's seller acceptance scenario as data, and
// the engine's decisions over it (testdata/seller-vectors, copied from the
// vectors the engine side generated at capsule-engine dae03af, #122's head).
type sellerCorpus struct {
	AcceptanceStep          string          `json:"acceptance_step"`
	TaskAuthorityRecord     json.RawMessage `json:"task_authority_record"`
	CommercialBoundsOpening json.RawMessage `json:"commercial_bounds_opening"`
	Steps                   []struct {
		Name            string                     `json:"name"`
		DryRun          bool                       `json:"dry_run"`
		CitesAcceptance bool                       `json:"cites_acceptance"`
		Action          map[string]json.RawMessage `json:"action"`
		// AuthorizationRecord is the approval record the step is checked
		// with, when it names one.
		AuthorizationRecord json.RawMessage `json:"authorization_record"`
	} `json:"steps"`
}

type sellerDecisions struct {
	Pack struct {
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Decisions map[string]struct {
		Outcome string            `json:"outcome"`
		Results map[string]string `json:"results"`
	} `json:"decisions"`
}

// sellerOnlyFields are the scenario's action fields this port's Action does
// not carry: only its empty extra, which no check reads (every field of
// capsule-engine d8373fb's Action the seller checks read is carried now).
var sellerOnlyFields = []string{"extra"}

// CROSS-IMPLEMENTATION, on the engine's own seller scenario, in order on one
// ledger: every check this port runs for asg/seller/0.1.0 gives the engine's
// result on every step, and every unported one is n/a, not yet run where the
// engine's check applies, never a pass or a fail. A step citing a mandate is
// refused by this port (verify_before_dispatch needs the capsule verifier),
// as it is for everyday; those steps are counted and asserted refused.
func TestSellerVectorsPortedChecksAgree(t *testing.T) {
	e, p := sellerEngine(t)
	corpus := decodeJSON[sellerCorpus](t, readFile(t, "testdata", "seller-vectors", "corpus.json"))
	py := decodeJSON[sellerDecisions](t, readFile(t, "testdata", "seller-vectors", "python-decisions.json"))
	if py.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors are for %s, the pack digests to %s", py.Pack.DefinitionDigest, p.DefinitionDigest)
	}
	var ledger []Record
	compared, refused := map[string]int{}, 0
	for _, step := range corpus.Steps {
		want, ok := py.Decisions[step.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", step.Name)
		}
		fields := maps.Clone(step.Action)
		for _, f := range sellerOnlyFields {
			delete(fields, f)
		}
		if step.CitesAcceptance {
			fields["cited_mandate_capsule_id"] = json.RawMessage(`"` + strings.Repeat("a", 64) + `"`)
		}
		body, err := json.Marshal(fields)
		if err != nil {
			t.Fatal(err)
		}
		a, err := decodeAction(body, true)
		if err != nil {
			t.Fatalf("%s: %v", step.Name, err)
		}
		// The ledger is what the engine sealed: its decision on every step,
		// a dry run marked so.
		rec := RecordFor(a, Decision{Outcome: want.Outcome})
		if step.DryRun {
			rec.DryRun = ptr(true)
		}
		d, err := e.checkWith(a, MemoryHistory(ledger), checkInputs{taskAuthority: corpus.TaskAuthorityRecord, opening: corpus.CommercialBoundsOpening, authorization: step.AuthorizationRecord})
		ledger = append(ledger, rec)
		if a.CitedMandateCapsuleID != nil {
			if !errors.Is(err, ErrCitedMandate) {
				t.Errorf("%s cites a mandate: %v, want the port's refusal", step.Name, err)
			}
			refused++
			continue
		}
		if err != nil {
			t.Fatalf("%s: %v", step.Name, err)
		}
		for _, c := range d.Constraints {
			engine, ok := want.Results[c.ID]
			if !ok {
				t.Errorf("%s: %s has no engine result", step.Name, c.ID)
				continue
			}
			if _, unported := sellerUnported[c.ID]; unported {
				if c.Result != NotApplicable {
					t.Errorf("%s: unported %s decided %s", step.Name, c.ID, c.Result)
				}
				// In scope, the engine's check decided; this port says so.
				if inScope := engine != string(NotApplicable); inScope && c.Pending() != NotYetRun {
					t.Errorf("%s: unported %s in scope, pending %q", step.Name, c.ID, c.Pending())
				}
				continue
			}
			if string(c.Result) != engine {
				t.Errorf("%s: %s Go %s, engine %s", step.Name, c.ID, c.Result, engine)
			}
			compared[c.ID]++
		}
	}
	if refused != 5 {
		t.Errorf("%d steps cite a mandate, the corpus has 5", refused)
	}
	// Every ported check was compared on every step the port ran.
	for _, c := range []string{"dedupe", "verify_before_dispatch", "action_class_gate", "recipient_role", "task_authority", "promise_never", "promise_requires_approval", "required_disclosure"} {
		if compared[c] != len(corpus.Steps)-refused {
			t.Errorf("%s compared on %d steps, want %d", c, compared[c], len(corpus.Steps)-refused)
		}
	}
}

func decodeJSON[T any](t *testing.T, b []byte) T {
	t.Helper()
	var out T
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// A deal's roles: none is absent, one is that role, two different ones (which
// capsule-cli refuses to seal) and an unknown one leave it unevaluable.
func TestPartyRoleFrom(t *testing.T) {
	for _, c := range []struct {
		roles         []string
		role          string
		present       bool
		whyMentioning string
	}{
		{nil, "", false, ""},
		{[]string{"seller", "seller"}, "seller", true, ""},
		{[]string{"buyer"}, "buyer", true, ""},
		{[]string{"seller", "buyer"}, "", false, "more than one party_role: buyer, seller"},
		{[]string{"broker"}, "broker", true, `party_role "broker" is not one this plugin knows`},
		{[]string{""}, "", true, `party_role "" is not one this plugin knows`},
	} {
		role, present, why := partyRoleFrom(c.roles)
		if role != c.role || present != c.present || (c.whyMentioning == "") != (why == "") || !strings.Contains(why, c.whyMentioning) {
			t.Errorf("%v: %q %v %q", c.roles, role, present, why)
		}
	}
}

// The checker input's party_role: set when the record states none, kept
// when it states the same, unevaluable when it states another, and a role
// the plugin has no pack for is named, never defaulted (a caller that skips
// the schema's enum).
func TestWithPartyRole(t *testing.T) {
	seller, buyer := "seller", "buyer"
	for _, c := range []struct {
		name   string
		record *string
		raw    string
		role   string
		why    string
	}{
		{"absent", nil, ``, "", ""},
		{"null", &seller, `null`, "seller", ""},
		{"set", nil, `"seller"`, "seller", ""},
		{"same", &seller, `"seller"`, "seller", ""},
		{"another", &buyer, `"seller"`, "buyer", "the checker input's party_role is not the one the checked record states"},
		{"unknown", nil, `"broker"`, "broker", `party_role "broker" is not one this plugin knows (buyer, seller)`},
	} {
		var raw json.RawMessage
		if c.raw != "" {
			raw = json.RawMessage(c.raw)
		}
		a := WithPartyRole(Action{PartyRole: c.record}, raw)
		role := ""
		if a.PartyRole != nil {
			role = *a.PartyRole
		}
		if role != c.role || strings.Join(a.Unevaluable(), "; ") != c.why {
			t.Errorf("%s: role %q unevaluable %v; want %q %q", c.name, role, a.Unevaluable(), c.role, c.why)
		}
	}
}

// AMENDMENT 4 on a real bundle: capsule-cli's seller bundle's typed offer
// (proposed-action/v0, no x-deal-v0) names its deal by chain_id, the same
// deal its x-deal-v0 baseline names by deal_id.
func TestTypedRecordNamesItsDealByChainID(t *testing.T) {
	raw := readFile(t, "testdata", "seller-bundle", "bundle.json")
	for id, source := range map[string]string{
		"13af2f81f9a1cd85f02346009282dc3b07e8f46550a6a2f82bb38ee4d8a29165": "chain_id",
		"9bc03e5ba7e2a4a1c2b3121c70b2967a81b4a1f917915fa414d5c38b5c96c4f1": "x-deal-v0.deal_id",
	} {
		a, err := DecodeRecord(raw, id)
		if err != nil {
			t.Fatal(err)
		}
		if a.DealID == nil || *a.DealID != "deal-04cc7725e632cdf0" || !slices.Contains(a.present, source) {
			t.Errorf("%s: deal %v, inputs %v; want deal-04cc7725e632cdf0 from %s", id[:8], a.DealID, a.present, source)
		}
	}
}
