package guard

import (
	"path/filepath"
	"slices"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// recipientRole12Vectors are seller.recipient_role/1.2.0's differential
// vectors (PROVENANCE.json): capsulectl rc20's pickup-area share with each
// recipient role and class mix, decided by capsule-engine 0c93a58's
// check_recipient_role under the 1.2.0 definition.
var recipientRole12Vectors = filepath.Join("testdata", "recipient-role-12-vectors")

const recipientRole12 = "seller.recipient_role/1.2.0"

// role12Engine is the seller 0.1.4 engine with s01 decided under
// seller.recipient_role/1.2.0's config, read from the vendored asg/seller/0.1.5
// definition and checked as NewEngine checks it.
func role12Engine(t *testing.T) *Engine {
	t.Helper()
	e, _ := seller014Engine(t)
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == recipientRole12 })
	if i < 0 {
		t.Fatalf("%s cites no %s", p.ID, recipientRole12)
	}
	if e.role, err = decodeConfig[roleConfig](p.Wickets[i]); err != nil {
		t.Fatal(err)
	}
	if err := checkRoleClasses(e.role, recipientRole12); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(e.role.RoleClasses["platform"], []string{"email", "pickup_area"}) {
		t.Fatalf("%s role_classes %v", recipientRole12, e.role.RoleClasses)
	}
	return e
}

// CROSS-IMPLEMENTATION: every vector decided as a live check decides s01,
// with NO known difference from the engine: the result, a fail's reason and
// the evidence. A platform passes given email, pickup_area or both, and
// fails given any other class or none.
func TestRecipientRole12AgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string          `json:"definition"`
		DefinitionDigest string          `json:"definition_digest"`
		Vectors          []releaseVector `json:"vectors"`
	}](t, readFile(t, recipientRole12Vectors, "expected.json"))
	if v.Definition != recipientRole12 || v.DefinitionDigest != portedDefinitions[recipientRole12] {
		t.Fatalf("vectors under %s %s, ported %s", v.Definition, v.DefinitionDigest, portedDefinitions[recipientRole12])
	}
	e := role12Engine(t)
	results := map[string]int{}
	for _, want := range v.Vectors {
		a, h, err := DecodeCheckInput(readFile(t, recipientRole12Vectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		c, err := e.constraintFor("recipient_role", a, h.ledger(), checkInputs{history: true, historyIncomplete: h.saleUnknown()})
		if err != nil {
			t.Fatal(err)
		}
		if got, engine := releaseCell(t, c), engineCell(t, want); got != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Input, got, engine)
		}
		results[want.Result]++
	}
	if len(v.Vectors) != 35 || results["pass"] != 17 || results["fail"] != 18 {
		t.Errorf("%d vectors, %v", len(v.Vectors), results)
	}
}
