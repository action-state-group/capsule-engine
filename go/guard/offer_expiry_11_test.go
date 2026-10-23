package guard

import (
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// offerExpiry11Vectors are offer_expiry/1.1.0's differential vectors
// (PROVENANCE.json): the records capsule-engine's own
// test_offer_expiry_no_proposal.py builds, as bundles replayed and as live
// check inputs, each with the engine's proposal_at and offer_expiry outcome.
var offerExpiry11Vectors = filepath.Join("testdata", "offer-expiry-11-vectors")

const offerExpiry11 = "offer_expiry/1.1.0"

type offerExpiryVector struct {
	releaseVector
	ProposalAt *string `json:"proposal_at"`
	NoProposal *bool   `json:"no_proposal"`
}

func offerExpiry11Config(t *testing.T) offerExpiryConfig {
	t.Helper()
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == offerExpiry11 })
	if i < 0 {
		t.Fatalf("%s cites no %s", p.ID, offerExpiry11)
	}
	c, err := decodeConfig[offerExpiryConfig](p.Wickets[i])
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(c.NoProposalClasses, []string{"marketplace.offer"}) {
		t.Fatalf("%s no_proposal_classes %v", offerExpiry11, c.NoProposalClasses)
	}
	return c
}

func strOrNil(s *string) string {
	if s == nil {
		return "<none>"
	}
	return *s
}

// CROSS-IMPLEMENTATION: every vector, the offer read as a sale bundle's
// record is read (DecodeRecord, its proposal_at from the message it
// answers) or as a live check input (DecodeCheckInput), with NO known
// difference from the engine: proposal_at, no_proposal, and offer_expiry's
// result, reason and evidence.
func TestOfferExpiry11AgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string              `json:"definition"`
		DefinitionDigest string              `json:"definition_digest"`
		Vectors          []offerExpiryVector `json:"vectors"`
	}](t, readFile(t, offerExpiry11Vectors, "expected.json"))
	if v.Definition != offerExpiry11 || v.DefinitionDigest != portedDefinitions[offerExpiry11] {
		t.Fatalf("vectors under %s %s, ported %s", v.Definition, v.DefinitionDigest, portedDefinitions[offerExpiry11])
	}
	c := offerExpiry11Config(t)
	results := map[string]int{}
	for _, want := range v.Vectors {
		raw := readFile(t, offerExpiry11Vectors, want.Input)
		var a Action
		var err error
		if want.CapsuleID != "" {
			a, err = DecodeRecord(raw, want.CapsuleID)
		} else {
			a, _, err = DecodeCheckInput(raw)
		}
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		if strOrNil(a.ProposalAt) != strOrNil(want.ProposalAt) {
			t.Errorf("%s: proposal_at %s, engine %s", want.Input, strOrNil(a.ProposalAt), strOrNil(want.ProposalAt))
		}
		if (a.NoProposal != nil && *a.NoProposal) != (want.NoProposal != nil && *want.NoProposal) {
			t.Errorf("%s: no_proposal %v, engine %v", want.Input, a.NoProposal, want.NoProposal)
		}
		got := checkOfferExpiry(a, c)
		engine := releaseOutcomeOf(t, want.Result, want.Reason, want.Evidence)
		if want.Result == "pass" {
			engine.Reason = ""
		}
		if strings.HasPrefix(want.Reason, "proposal expired: ") {
			// Muse has worded an expired proposal as "the proposal is Ns old,
			// ..." since its 1.0.1 port (66b6d22), where the engine says
			// "proposal expired: it is Ns old, ...": the same facts, whose
			// evidence digest is compared.
			engine.Reason = "the proposal is " + strings.TrimPrefix(want.Reason, "proposal expired: it is ")
		}
		if cell := releaseCell(t, got); cell != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Input, cell, engine)
		}
		results[want.Result]++
	}
	if len(v.Vectors) != 36 || results["pass"] == 0 || results["fail"] == 0 || results["n/a"] != 2 {
		t.Errorf("%d vectors, %v", len(v.Vectors), results)
	}
}

// 1.0.1 reads none of this: an offer sealed no_proposal still needs its
// proposal_at, as the engine's test_1_0_1_does_not_read_no_proposal pins.
func TestOfferExpiry101DoesNotReadNoProposal(t *testing.T) {
	c := offerExpiry11Config(t)
	c.NoProposalClasses = nil
	a, _, err := DecodeCheckInput(readFile(t, offerExpiry11Vectors, "live", "no-proposal.json"))
	if err != nil {
		t.Fatal(err)
	}
	if got := checkOfferExpiry(a, c); got.Result != Fail || got.failNote != "the action carries no proposal_at; the offer's expiry could not be established" {
		t.Errorf("1.0.1: %s %q", got.Result, got.failNote)
	}
	a, _, err = DecodeCheckInput(readFile(t, offerExpiry11Vectors, "live", "no-proposal-beside-proposal-at.json"))
	if err != nil {
		t.Fatal(err)
	}
	if got := checkOfferExpiry(a, c); got.Result != Pass {
		t.Errorf("1.0.1 beside a proposal_at: %s %q", got.Result, got.failNote)
	}
}
