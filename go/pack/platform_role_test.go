package pack

import (
	"testing"
)

// capsule-engine #173 (7339028): asg/everyday/0.3.7 and asg/seller/0.1.4
// add a platform recipient that may be given the user's email.
const (
	everyday037     = "c1cea374102bba60de82e16663c550a278ee07a399c5faeb285fe1ea1ea27d4d"
	everyday036     = "8a8e0a7e1ca4319b6e45f2037f4af49e6592919f373e59874ce1ead7249b073d"
	seller014Digest = "c14c8f8176a933f97c8a04064f6634f8f0e422178f71c4a531ac01631f529e5b"
)

// The platform packs load at the digests the engine computed at 7339028, and
// the earlier ones still load at theirs. Which pack a deal runs by default is
// the caller's choice, tested where that choice is made.
func TestThePlatformPacksLoadAtTheirDigests(t *testing.T) {
	for _, c := range []struct {
		load func() (Pack, error)
		want identity
	}{{LoadEveryday037, identity{"asg/everyday/0.3.7", everyday037}}, {LoadSeller014, identity{"asg/seller/0.1.4", seller014Digest}},
		{LoadEveryday036, identity{"asg/everyday/0.3.6", everyday036}}, {LoadSeller013, identity{"asg/seller/0.1.3", seller013Digest}}} {
		p, err := c.load()
		if err != nil {
			t.Fatal(err)
		}
		if got := identityOf(p); got != c.want {
			t.Errorf("frozen %+v, want %+v", got, c.want)
		}
	}
}

// samePackBut holds p to old: the same wickets in the same order but the
// ones replaced names (old id -> new id), the same folds, and the same rules
// but the statements of the rules in reworded.
func samePackBut(t *testing.T, p, old Pack, replaced map[string]string, reworded ...string) {
	t.Helper()
	if len(p.Wickets) != len(old.Wickets) {
		t.Fatalf("%s cites %d wickets, %s %d", p.ID, len(p.Wickets), old.ID, len(old.Wickets))
	}
	seen := 0
	for i, w := range p.Wickets {
		o := old.Wickets[i]
		if to, ok := replaced[o.ID]; ok {
			seen++
			if w.ID != to || w.Digest == o.Digest {
				t.Errorf("wicket %d: %s %s in place of %s %s", i, w.ID, w.Digest, o.ID, o.Digest)
			}
			continue
		}
		if w.ID != o.ID || w.Digest != o.Digest {
			t.Errorf("wicket %d: %s %s, %s %s", i, w.ID, w.Digest, o.ID, o.Digest)
		}
	}
	if seen != len(replaced) {
		t.Errorf("%d of %d replaced wickets found", seen, len(replaced))
	}
	if len(p.Folds) != len(old.Folds) {
		t.Errorf("%s cites %d folds, %s %d", p.ID, len(p.Folds), old.ID, len(old.Folds))
	}
	for _, f := range old.Folds {
		if g, ok := p.Fold(f.ID); !ok || g.Digest != f.Digest {
			t.Errorf("fold %s: %+v, was %s", f.ID, g, f.Digest)
		}
	}
	if len(p.Rules) != len(old.Rules) {
		t.Fatalf("%s %d rules, %s %d", p.ID, len(p.Rules), old.ID, len(old.Rules))
	}
	changed := map[string]bool{}
	for _, id := range reworded {
		changed[id] = true
	}
	for i, r := range p.Rules {
		o := old.Rules[i]
		if changed[r.ID] {
			if r.Statement == o.Statement {
				t.Errorf("rule %s: statement unchanged", r.ID)
			}
			r.Statement = o.Statement
		}
		if r != o {
			t.Errorf("rule %d differs:\n%s %+v\n%s %+v", i, p.ID, r, old.ID, o)
		}
	}
}

// 0.3.7 is 0.3.6 with recipient_role/1.1.0 and action_class_gate/1.1.0 in
// place of their 1.0.0 definitions, and only r03, r11 and r12 reworded.
// So vectors the engine decided under 0.3.6 that disclose nothing to a
// platform still hold the checks 0.3.7 runs.
func TestEveryday037Is036WithThePlatform(t *testing.T) {
	p, err := LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadEveryday036()
	if err != nil {
		t.Fatal(err)
	}
	samePackBut(t, p, old, map[string]string{"recipient_role/1.0.0": "recipient_role/1.1.0",
		"action_class_gate/1.0.0": "action_class_gate/1.1.0"},
		"r03-shipping-address-to-fulfilling-merchant", "r11-home-address-to-an-individual", "r12-personal-contact-to-a-new-party")
}

// 0.1.4 is 0.1.3 with seller.recipient_role/1.1.0 and
// seller.release_on_acceptance/1.1.0, and only s01 and s13 reworded.
func TestSeller014Is013WithThePlatform(t *testing.T) {
	p, err := LoadSeller014()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadSeller013()
	if err != nil {
		t.Fatal(err)
	}
	samePackBut(t, p, old, map[string]string{"seller.recipient_role/1.0.0": "seller.recipient_role/1.1.0",
		"seller.release_on_acceptance/1.0.0": "seller.release_on_acceptance/1.1.0"},
		"s01-personal-data-to-the-buyer-or-the-user", "s13-personal-data-only-to-the-buyer-who-accepted")
}
