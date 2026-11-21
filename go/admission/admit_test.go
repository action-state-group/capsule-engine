package admission

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// The shared admit vectors (conformance-vectors/admission/v0/admit.json):
// each names a case of cases.json and the caller's refs, and expects the
// x-deal-v1 admit body as JCS text, or null for a decision without a tier.
// This test writes "admit" with -update; the Rust port checks it byte for
// byte.

type admitSet struct {
	Source string      `json:"source"`
	Cases  []admitCase `json:"cases"`
}

type admitCase struct {
	Case       string   `json:"case"`
	Asks       []string `json:"asks"`
	Answers    []string `json:"answers"`
	CardDigest string   `json:"card_digest"`
	Nonce      string   `json:"nonce"`
	Admit      *string  `json:"admit"`
	AdmitsDeal *bool    `json:"admits_deal"`
}

func TestAdmitVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(vectorDir, "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases vectorSet
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	byName := map[string]vectorCase{}
	for _, c := range cases.Cases {
		byName[c.Case] = c
	}
	path := filepath.Join(vectorDir, "admit.json")
	raw, err = os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var set admitSet
	if err := json.Unmarshal(raw, &set); err != nil {
		t.Fatal(err)
	}
	if len(set.Cases) == 0 {
		t.Fatal("no admit vectors")
	}
	verdicts := map[string]bool{}
	for i := range set.Cases {
		a := &set.Cases[i]
		c, ok := byName[a.Case]
		if !ok {
			t.Fatalf("%s: no such case in cases.json", a.Case)
		}
		src, err := os.ReadFile(filepath.Join(vectorDir, filepath.FromSlash(c.Pack)))
		if err != nil {
			t.Fatal(err)
		}
		p, err := Load(src)
		if err != nil {
			t.Fatal(err)
		}
		d := Evaluate(p, decodeInput(t, c.Input))
		verdicts[d.Verdict] = true
		var got *string
		body, err := d.Admit(AdmitRefs{Asks: a.Asks, Answers: a.Answers, CardDigest: a.CardDigest, Nonce: a.Nonce})
		switch {
		case errors.Is(err, ErrNoTier):
			if d.Tier != "" {
				t.Errorf("%s: ErrNoTier at tier %s", a.Case, d.Tier)
			}
		case err != nil:
			t.Fatalf("%s: %v", a.Case, err)
		default:
			s, err := canon.JCS(body)
			if err != nil {
				t.Fatal(err)
			}
			got = &s
		}
		if *update {
			a.Admit, a.AdmitsDeal = got, &d.AdmitsDeal
		} else if deref(got) != deref(a.Admit) {
			t.Errorf("%s: admit\n got %s\nwant %s", a.Case, deref(got), deref(a.Admit))
		} else if a.AdmitsDeal == nil || *a.AdmitsDeal != d.AdmitsDeal {
			t.Errorf("%s: admits_deal %v, want %v", a.Case, d.AdmitsDeal, a.AdmitsDeal)
		}
	}
	for _, v := range []string{GO, ASK, LIMIT, NO} {
		if !verdicts[v] {
			t.Errorf("no admit vector decides %s", v)
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

func TestAdmitBody(t *testing.T) {
	d := Decision{Verdict: GO, Source: SourceRows, Tier: "T1", Pack: PackRef{ID: "asg/x/1.0.0", Digest: strings.Repeat("a", 64)},
		Rows: []RowResult{{Row: "owner-signed"}, {Row: "card-verified"}}}
	hex := func(c string) string { return strings.Repeat(c, 64) }
	body, err := d.Admit(AdmitRefs{Asks: []string{hex("1")}, Answers: []string{}, CardDigest: hex("c"), Nonce: hex("e")})
	if err != nil {
		t.Fatal(err)
	}
	s, err := canon.JCS(body)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"answers":[],"asks":[{"digest":"` + hex("1") + `","digest_alg":"SHA-256","type":"deal-record"}],"card_digest":"` + hex("c") +
		`","nonce":"` + hex("e") + `","outcome":"GO","pack_digest":"` + hex("a") + `","rows":["owner-signed","card-verified"],"tier":"T1"}`
	if s != want {
		t.Errorf("admit\n got %s\nwant %s", s, want)
	}

	// No tier, no admit.
	no := Decision{Verdict: NO, Source: SourceMissing}
	if _, err := no.Admit(AdmitRefs{CardDigest: hex("c"), Nonce: hex("e")}); !errors.Is(err, ErrNoTier) {
		t.Errorf("a decision without a tier: %v, want ErrNoTier", err)
	}
	// Malformed refs are refused whatever the verdict, a NO included.
	if _, err := no.Admit(AdmitRefs{}); err == nil || errors.Is(err, ErrNoTier) {
		t.Errorf("a NO with malformed refs: %v, want a refusal of the refs", err)
	}

	// Each caller-held member must be 64 lowercase hex.
	ok := AdmitRefs{Asks: []string{}, Answers: []string{}, CardDigest: hex("c"), Nonce: hex("e")}
	for name, edit := range map[string]func(r *AdmitRefs){
		"an ask that is not a digest":  func(r *AdmitRefs) { r.Asks = []string{"x"} },
		"an answer in upper case":      func(r *AdmitRefs) { r.Answers = []string{hex("A")} },
		"a card digest one hex short":  func(r *AdmitRefs) { r.CardDigest = hex("c")[1:] },
		"no card digest":               func(r *AdmitRefs) { r.CardDigest = "" },
		"no nonce":                     func(r *AdmitRefs) { r.Nonce = "" },
		"a nonce that is not 256 bits": func(r *AdmitRefs) { r.Nonce = hex("e") + "e" },
	} {
		r := ok
		edit(&r)
		if _, err := d.Admit(r); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	// nil asks and answers are written as empty arrays, as the profile requires.
	body, err = d.Admit(AdmitRefs{CardDigest: hex("c"), Nonce: hex("e")})
	if err != nil {
		t.Fatal(err)
	}
	if s, _ := canon.JCS(body); !strings.Contains(s, `"answers":[]`) || !strings.Contains(s, `"asks":[]`) {
		t.Errorf("nil refs: %s", s)
	}
}
