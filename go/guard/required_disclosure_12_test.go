package guard

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// requiredDisclosure12Vectors are required_disclosure/1.2.0's differential
// vectors (PROVENANCE.json): capsulectl rc20's offer and commit checks, with
// and without their deal claims, with each listing_classes value, decided
// live by capsule-engine 0c93a58's own CONFIGURED_CHECKS under 1.2.0.
var requiredDisclosure12Vectors = filepath.Join("testdata", "required-disclosure-12-vectors")

const requiredDisclosure12 = "required_disclosure/1.2.0"

type listingVector struct {
	releaseVector
	ListingClasses []string `json:"listing_classes"`
}

func loadRequiredDisclosure12(t *testing.T) (requiredDisclosure, pack.Wicket, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == requiredDisclosure12 })
	if i < 0 {
		t.Fatalf("%s cites no %s", p.ID, requiredDisclosure12)
	}
	rd, err := newRequiredDisclosure(p.Wickets[i], p)
	if err != nil {
		t.Fatal(err)
	}
	if !rd.countListing || rd.statements == nil {
		t.Fatalf("%s: count_listing_classes %v, statement fold %v", requiredDisclosure12, rd.countListing, rd.statements != nil)
	}
	return rd, p.Wickets[i], p
}

// museListWording is the engine's reason as Muse words it: a list of
// classes as "a, b", without Python's repr, as Muse has worded s05 since
// its port.
func museListWording(reason string) string {
	return strings.NewReplacer("'", "", "[", "", "]", "").Replace(reason)
}

// CROSS-IMPLEMENTATION: every vector decided as CheckWithHistory decides
// s05 (the history and the deal claims its ledger), with NO known
// difference from the engine: the listing_classes read, and the result, a
// fail's reason (in Muse's wording) and the evidence.
func TestRequiredDisclosure12AgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string          `json:"definition"`
		DefinitionDigest string          `json:"definition_digest"`
		Vectors          []listingVector `json:"vectors"`
	}](t, readFile(t, requiredDisclosure12Vectors, "expected.json"))
	if v.Definition != requiredDisclosure12 || v.DefinitionDigest != portedDefinitions[requiredDisclosure12] {
		t.Fatalf("vectors under %s %s, ported %s", v.Definition, v.DefinitionDigest, portedDefinitions[requiredDisclosure12])
	}
	rd, _, _ := loadRequiredDisclosure12(t)
	results := map[string]int{}
	for _, want := range v.Vectors {
		a, h, err := DecodeCheckInput(readFile(t, requiredDisclosure12Vectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		if (a.ListingClasses == nil) != (want.ListingClasses == nil) || !slices.Equal(a.ListingClasses, want.ListingClasses) {
			t.Errorf("%s: listing_classes %v, engine %v", want.Input, a.ListingClasses, want.ListingClasses)
		}
		got := checkRequiredDisclosure(a, h.ledger(), rd)
		engine := releaseOutcomeOf(t, want.Result, museListWording(want.Reason), want.Evidence)
		if want.Result != "fail" {
			engine.Reason = ""
		}
		if cell := releaseCell(t, got); cell != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Input, cell, engine)
		}
		results[want.Result]++
	}
	if len(v.Vectors) != 40 || results["pass"] != 28 || results["fail"] != 12 {
		t.Errorf("%d vectors, %v", len(v.Vectors), results)
	}
}

// count_listing_classes without the statement fold is refused when the
// engine is built, as required_disclosure.py refuses it; on --action a
// listing_classes that is not sorted class tokens each named once is
// refused, as the engine's Action refuses it, and an empty one is kept.
func TestListingClassesShapes(t *testing.T) {
	_, w, p := loadRequiredDisclosure12(t)
	var cfg map[string]any
	if err := w.Config.Decode(&cfg); err != nil {
		t.Fatal(err)
	}
	delete(cfg, "statement_fold_id")
	delete(cfg, "statement_fold_digest")
	if err := w.Config.Encode(cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := newRequiredDisclosure(w, p); !errors.Is(err, errListingWithoutStatements) {
		t.Errorf("count_listing_classes with no statement fold: %v", err)
	}
	for listing, ok := range map[string]bool{`[]`: true, `["condition"]`: true, `["condition","price"]`: true,
		`["price","condition"]`: false, `["condition","condition"]`: false, `["Condition"]`: false, `[""]`: false} {
		body := json.RawMessage(`{"verb":"offer","operator":"o","developer":"d","timestamp":"2026-10-10T12:00:00Z","listing_classes":` + listing + `}`)
		a, err := decodeAction(body, true)
		if (err == nil) != ok {
			t.Errorf("--action listing_classes %s: %v", listing, err)
		}
		if ok && a.ListingClasses == nil {
			t.Errorf("--action listing_classes %s: dropped", listing)
		}
	}
}
