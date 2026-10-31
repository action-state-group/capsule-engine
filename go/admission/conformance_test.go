package admission

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// The shared admission vectors (conformance-vectors/admission/v0): each
// case's verdict, source and tier are written by hand; block_sha256, the
// SHA-256 of the decision block's JCS bytes, is written by this test with
// -update and checked by the Rust port byte for byte.

var update = flag.Bool("update", false, "rewrite block_sha256 in the admission vectors")

const vectorDir = "../../conformance-vectors/admission/v0"

type vectorSet struct {
	Source string       `json:"source"`
	Cases  []vectorCase `json:"cases"`
}

type vectorCase struct {
	Case     string          `json:"case"`
	Pack     string          `json:"pack"`
	Input    json.RawMessage `json:"input"`
	Expected vectorExpected  `json:"expected"`
}

type vectorExpected struct {
	Verdict     string  `json:"verdict"`
	Source      string  `json:"source"`
	Tier        *string `json:"tier"`
	BlockSHA256 string  `json:"block_sha256,omitempty"`
}

// vectorInput is Inputs as a vector writes it.
type vectorInput struct {
	Now      string `json:"now"`
	Amount   string `json:"amount"`
	Currency string `json:"currency"`
	Platform struct {
		ReleaseAttestation string `json:"release_attestation"`
		OwnerSigned        *bool  `json:"owner_signed"`
	} `json:"platform"`
	Card struct {
		State              string `json:"state"`
		FirstCheckpointAt  string `json:"first_checkpoint_at"`
		LatestCheckpointAt string `json:"latest_checkpoint_at"`
		Witness            string `json:"witness"`
		Controller         string `json:"controller"`
		Claims             *struct {
			Held  int64 `json:"held"`
			Total int64 `json:"total"`
		} `json:"claims"`
		Disputes *struct {
			Open int64 `json:"open"`
			Of   int64 `json:"of"`
		} `json:"disputes"`
		Bands *[]struct {
			Currency string `json:"currency"`
			Decade   int    `json:"decade"`
			Subjects int64  `json:"subjects"`
			Adverse  int64  `json:"adverse"`
		} `json:"bands"`
	} `json:"card"`
	Selection struct {
		State              string   `json:"state"`
		UnansweredDisputes []string `json:"unanswered_disputes"`
	} `json:"selection"`
	Own *struct {
		Exchanges   int64 `json:"exchanges"`
		Settled     int64 `json:"settled"`
		HeldHalves  int64 `json:"held_halves"`
		Conflicting int64 `json:"conflicting"`
		Verdicts    []struct {
			At      string `json:"at"`
			Adverse bool   `json:"adverse"`
		} `json:"verdicts"`
	} `json:"own"`
	Operator struct {
		DenyListed  bool  `json:"deny_listed"`
		AllowListed *bool `json:"allow_listed"`
	} `json:"operator"`
	Vouches []struct {
		Controller string `json:"controller"`
	} `json:"vouches"`
	Stake *struct {
		Amount   string `json:"amount"`
		Currency string `json:"currency"`
		Rail     struct {
			ID     string `json:"id"`
			Digest string `json:"digest"`
		} `json:"rail"`
	} `json:"stake"`
	Subject string `json:"subject"`
	Judged  map[string]struct {
		Met   bool   `json:"met"`
		Judge string `json:"judge"`
	} `json:"judged"`
	Asks     map[string]string `json:"asks"`
	AsksMade int               `json:"asks_made"`
}

func parseTime(t *testing.T, s string) time.Time {
	t.Helper()
	if s == "" {
		return time.Time{}
	}
	at, err := time.Parse(time.RFC3339, s)
	if err != nil {
		t.Fatal(err)
	}
	return at
}

func decodeInput(t *testing.T, raw json.RawMessage) Inputs {
	t.Helper()
	var v vectorInput
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&v); err != nil {
		t.Fatal(err)
	}
	in := Inputs{
		Now: parseTime(t, v.Now), Amount: v.Amount, Currency: v.Currency,
		Platform: Platform{ReleaseAttestation: v.Platform.ReleaseAttestation, OwnerSigned: v.Platform.OwnerSigned},
		Card: Card{State: v.Card.State, FirstCheckpointAt: parseTime(t, v.Card.FirstCheckpointAt),
			LatestCheckpointAt: parseTime(t, v.Card.LatestCheckpointAt), Witness: v.Card.Witness, Controller: v.Card.Controller},
		Selection: Selection{State: v.Selection.State},
		Operator:  Operator{DenyListed: v.Operator.DenyListed, AllowListed: v.Operator.AllowListed},
		Subject:   v.Subject, Asks: v.Asks, AsksMade: v.AsksMade,
	}
	if v.Card.Claims != nil {
		in.Card.Claims = &Claims{Held: v.Card.Claims.Held, Total: v.Card.Claims.Total}
	}
	if v.Card.Disputes != nil {
		in.Card.Disputes = &Disputes{Open: v.Card.Disputes.Open, Of: v.Card.Disputes.Of}
	}
	if v.Card.Bands != nil {
		in.Card.Bands = []BandCount{}
		for _, b := range *v.Card.Bands {
			in.Card.Bands = append(in.Card.Bands, BandCount{Currency: b.Currency, Decade: b.Decade, Subjects: b.Subjects, Adverse: b.Adverse})
		}
	}
	for _, at := range v.Selection.UnansweredDisputes {
		in.Selection.UnansweredDisputes = append(in.Selection.UnansweredDisputes, parseTime(t, at))
	}
	if v.Judged != nil {
		in.Judged = map[string]Judgement{}
		for row, j := range v.Judged {
			in.Judged[row] = Judgement{Met: j.Met, Judge: j.Judge}
		}
	}
	if v.Own != nil {
		in.Own = &OwnHistory{Exchanges: v.Own.Exchanges, Settled: v.Own.Settled, HeldHalves: v.Own.HeldHalves, Conflicting: v.Own.Conflicting}
		for _, vd := range v.Own.Verdicts {
			in.Own.Verdicts = append(in.Own.Verdicts, Verdict{At: parseTime(t, vd.At), Adverse: vd.Adverse})
		}
	}
	if v.Stake != nil {
		in.Stake = &Stake{Amount: v.Stake.Amount, Currency: v.Stake.Currency,
			Rail: RailPin{ID: v.Stake.Rail.ID, Digest: v.Stake.Rail.Digest}}
	}
	for _, vc := range v.Vouches {
		in.Vouches = append(in.Vouches, Vouch{Controller: vc.Controller})
	}
	return in
}

func TestConformanceVectors(t *testing.T) {
	path := filepath.Join(vectorDir, "cases.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var set vectorSet
	if err := json.Unmarshal(raw, &set); err != nil {
		t.Fatal(err)
	}
	if len(set.Cases) == 0 {
		t.Fatal("no admission vectors")
	}
	packs := map[string]*Pack{}
	for i := range set.Cases {
		c := &set.Cases[i]
		p, ok := packs[c.Pack]
		if !ok {
			src, err := os.ReadFile(filepath.Join(vectorDir, filepath.FromSlash(c.Pack)))
			if err != nil {
				t.Fatal(err)
			}
			if p, err = Load(src); err != nil {
				t.Fatalf("%s: %v", c.Pack, err)
			}
			packs[c.Pack] = p
		}
		d := Evaluate(p, decodeInput(t, c.Input))
		jcs, err := canon.JCS(d.Block())
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256([]byte(jcs))
		got := hex.EncodeToString(sum[:])
		var tier *string
		if d.Tier != "" {
			tier = &d.Tier
		}
		if d.Verdict != c.Expected.Verdict || d.Source != c.Expected.Source || !samePtr(tier, c.Expected.Tier) {
			t.Errorf("%s: got %s from %s at %v; want %s from %s at %v\n%s", c.Case, d.Verdict, d.Source, deref(tier),
				c.Expected.Verdict, c.Expected.Source, deref(c.Expected.Tier), jcs)
			continue
		}
		if *update {
			c.Expected.BlockSHA256 = got
		} else if got != c.Expected.BlockSHA256 {
			t.Errorf("%s: block_sha256 %s, want %s\n%s", c.Case, got, c.Expected.BlockSHA256, jcs)
		}
	}
	if *update && !t.Failed() {
		out, err := json.MarshalIndent(set, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func samePtr(a, b *string) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func deref(s *string) string {
	if s == nil {
		return "<none>"
	}
	return *s
}
