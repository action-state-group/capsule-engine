package guard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// capsule-engine#182: no reason of an action_class_gate rule names a selector
// or an action class. r11 and r12 read "Asks: first time sharing your {class}
// with {party}" from what recipient_role recorded; the others are generic.
func everyday039Engine(t *testing.T) *Engine {
	t.Helper()
	p, err := pack.LoadEveryday039()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func share(role string, classes []string) Action {
	return Action{Verb: "share", Operator: "op", Developer: "dev", ActionClass: strp("disclosure.personal"), ActionID: "share/x",
		ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z", RecipientRole: strp(role), DisclosedClasses: classes}
}

func TestR11AndR12SayWhoGetsWhat(t *testing.T) {
	e := everyday039Engine(t)
	for _, c := range []struct {
		role    string
		classes []string
		want    string
	}{
		{"fulfilling_merchant", []string{"phone"}, "Asks: first time sharing your phone with the merchant."},
		{"fulfilling_merchant", []string{"phone", "email"}, "Asks: first time sharing your email and phone with the merchant."},
		{"third_party", []string{"home_address", "email", "phone"}, "Asks: first time sharing your email, home address and phone with a third party."},
		{"buyer", []string{"phone"}, "Asks: first time sharing your phone with the buyer."},
		{"fulfilling_merchant", nil, "Asks: first time sharing your details with this recipient."},
	} {
		_, rules := reportOf(t, e, share(c.role, c.classes), nil)
		for _, id := range []string{"r11-home-address-to-an-individual", "r12-personal-contact-to-a-new-party"} {
			if r := rules[id]; r.Verdict != VerdictFail || r.Reason != c.want {
				t.Errorf("%s to %s %v: %s %q, want fail %q", id, c.role, c.classes, r.Verdict, r.Reason, c.want)
			}
		}
	}
}

func TestEveryOtherGateReasonIsGeneric(t *testing.T) {
	e := everyday039Engine(t)
	booking := Action{Verb: "book", Operator: "op", Developer: "dev", ActionClass: strp("booking.create"), ActionID: "book/x",
		ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z"}
	unmapped := Action{Verb: "do", Operator: "op", Developer: "dev", ActionClass: strp("household.unlisted"), ActionID: "do/x",
		ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z"}
	cases := map[string]Action{"booking": booking, "unmapped": unmapped, "platform": share("platform", []string{"email"}),
		"merchant": share("fulfilling_merchant", []string{"phone"})}
	want := map[string]map[string]string{
		"booking":  {"r16-booking-with-a-commitment": gateFlagged, "r15-public-posting": gateNotApplicable, "r11-home-address-to-an-individual": gateNotApplicable},
		"unmapped": {"r16-booking-with-a-commitment": gateUnrecognised, "r01-research-and-prepare": gateUnrecognised},
		"platform": {"r11-home-address-to-an-individual": gateNotApplicable, "r12-personal-contact-to-a-new-party": gateNotApplicable},
		"merchant": {"r16-booking-with-a-commitment": gateNotApplicable, "r01-research-and-prepare": gateNotApplicable},
	}
	for name, a := range cases {
		_, rules := reportOf(t, e, a, nil)
		for id := range gateRules {
			r := rules[id]
			for _, banned := range []string{"selector", "action class", "disclosure.personal", "booking.create", "household.unlisted", "non_consequential"} {
				if strings.Contains(r.Reason, banned) {
					t.Errorf("%s %s: reason %q names %q", name, id, r.Reason, banned)
				}
			}
			if w, ok := want[name][id]; ok && r.Reason != w {
				t.Errorf("%s %s: reason %q, want %q", name, id, r.Reason, w)
			}
		}
	}
}

// The sentences are the table Python's tests read too, so a change in one
// language alone fails.
func TestTheSentencesAreTheSharedTable(t *testing.T) {
	var table struct {
		NotApplicable     string `json:"not_applicable"`
		Flagged           string `json:"flagged"`
		Unrecognised      string `json:"unrecognised"`
		FirstShareUnknown string `json:"first_share_unknown"`
		FirstShare        []struct {
			Role    string   `json:"role"`
			Classes []string `json:"classes"`
			Reason  string   `json:"reason"`
		} `json:"first_share"`
	}
	raw, err := os.ReadFile(filepath.Join(conformanceDir(), "..", "reasons", "gate-reasons.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatal(err)
	}
	if gateNotApplicable != table.NotApplicable || gateFlagged != table.Flagged || gateUnrecognised != table.Unrecognised ||
		gateFirstShareUnknown != table.FirstShareUnknown {
		t.Errorf("constants %q %q %q %q differ from the table", gateNotApplicable, gateFlagged, gateUnrecognised, gateFirstShareUnknown)
	}
	for _, c := range table.FirstShare {
		if got := shareReason(&recordedShare{role: c.Role, classes: c.Classes}); got != c.Reason {
			t.Errorf("%s %v: %q, want %q", c.Role, c.Classes, got, c.Reason)
		}
	}
}

// The report's reason is a snapshot: editing the caller's slice after the
// check does not change it.
func TestTheReasonSnapshotsTheRecordedClasses(t *testing.T) {
	e := everyday039Engine(t)
	classes := []string{"phone"}
	a := share("fulfilling_merchant", classes)
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatal(err)
	}
	classes[0] = "email"
	for _, r := range e.Report(a, d).Rules {
		if r.Rule == "r11-home-address-to-an-individual" && r.Reason != "Asks: first time sharing your phone with the merchant." {
			t.Errorf("reason %q changed with the caller's slice", r.Reason)
		}
	}
}

// A check held because the record names another taxonomy keeps its own
// reason, which names the two versions and no selector or class.
func TestAGateHeldForAnotherTaxonomyKeepsItsReason(t *testing.T) {
	e := everyday039Engine(t)
	a := Action{Verb: "book", Operator: "op", Developer: "dev", ActionClass: strp("booking.create"), ActionID: "book/x",
		ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z", TaxonomyVersion: strp("1")}
	_, rules := reportOf(t, e, a, nil)
	r := rules["r16-booking-with-a-commitment"]
	if r.Verdict != VerdictNotEvaluable || !strings.Contains(r.Reason, "taxonomy") || strings.Contains(r.Reason, "selector") ||
		strings.Contains(r.Reason, "booking.create") {
		t.Errorf("%+v", r)
	}
}
