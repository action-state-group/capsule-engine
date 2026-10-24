package pack

import (
	"strings"
	"testing"
)

// The rc21 pack cut (capsule-engine main 0c93a58, seller 0.1.5 re-vendored
// at a489e49 after #209 reworded its s04 reason): each pack loads at the
// definition digest its PROVENANCE.json records as the engine computed it.
func TestTheRC21PacksLoadAtTheCutDigests(t *testing.T) {
	for _, c := range []struct {
		load       func() (Pack, error)
		id, digest string
	}{
		{LoadSeller015, "asg/seller/0.1.5", "81013aedbc3d930dcb9a4871d932373eca9de6968e5cff9fb450158d1fcc576a"},
		{LoadEveryday037, "asg/everyday/0.3.7", "c1cea374102bba60de82e16663c550a278ee07a399c5faeb285fe1ea1ea27d4d"},
		{LoadEveryday038, "asg/everyday/0.3.8", "ac7697061fd1c50177000648e2458f6236068679fc67ae924870ee8fb1cd8488"},
		// The #182 wording versions: the released ones stay at their own digests.
		{LoadSeller016, "asg/seller/0.1.6", "5e112a7f5136e2193adb177605dfad10b7b0753a2de93ed72e446695b7acc56a"},
		{LoadEveryday039, "asg/everyday/0.3.9", "acb8a0f7073b872e0a1cc367997ace49dd69fd4e63fe60996c328d8b324ac4a7"},
	} {
		p, err := c.load()
		if err != nil {
			t.Fatalf("%s: %v", c.id, err)
		}
		if p.ID != c.id || p.DefinitionDigest != c.digest {
			t.Errorf("%s %s, want %s %s", p.ID, p.DefinitionDigest, c.id, c.digest)
		}
	}
}

// 0.1.5 is 0.1.4 with seven seller definitions replaced, and only the
// rules that cite them reworded (s07 cites seller.destination_rail).
func TestSeller015Is014WithTheRC21Definitions(t *testing.T) {
	p, err := LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadSeller014()
	if err != nil {
		t.Fatal(err)
	}
	samePackBut(t, p, old, map[string]string{
		"seller.recipient_role/1.1.0":        "seller.recipient_role/1.2.0",
		"promise_requires_approval/1.0.0":    "promise_requires_approval/1.1.0",
		"promise_never/1.0.0":                "promise_never/1.1.0",
		"required_disclosure/1.1.0":          "required_disclosure/1.2.0",
		"offer_expiry/1.0.1":                 "offer_expiry/1.1.0",
		"seller.destination_rail/1.0.0":      "seller.destination_rail/1.1.0",
		"seller.release_on_acceptance/1.1.0": "seller.release_on_acceptance/1.2.0",
	}, "s01-personal-data-to-the-buyer-or-the-user", "s04-statement-that-needs-the-users-word",
		"s05-required-statement-made-first", "s06-proposal-still-current", "s07-payment-rail-the-user-allows",
		"s10-statement-never-made", "s13-personal-data-only-to-the-buyer-who-accepted")
}

// 0.3.8 is 0.3.7 with action_class_gate/1.2.0, and only r11 and r12
// changed: reworded, and each on a told-before selector of 1.2.0 in place
// of personal_disclosure.
func TestEveryday038Is037WithToldBefore(t *testing.T) {
	p, err := LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	selectors := map[string]string{"r11-home-address-to-an-individual": "personal_disclosure_new_class",
		"r12-personal-contact-to-a-new-party": "personal_disclosure_new_party"}
	old.Rules = append([]Rule(nil), old.Rules...)
	for i, r := range old.Rules {
		if to, ok := selectors[r.ID]; ok {
			if r.Selector != "personal_disclosure" {
				t.Errorf("%s: 0.3.7 selector %s", r.ID, r.Selector)
			}
			old.Rules[i].Selector = to
		}
	}
	samePackBut(t, p, old, map[string]string{"action_class_gate/1.1.0": "action_class_gate/1.2.0"},
		"r11-home-address-to-an-individual", "r12-personal-contact-to-a-new-party")
}

// 0.3.9 is 0.3.8 reworded (capsule-engine#182): every wicket, fold, id and
// check is 0.3.8's, and only r03's statement changed.
func TestEveryday039IsEveryday038ReWorded(t *testing.T) {
	p, err := LoadEveryday039()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	samePackBut(t, p, old, nil, "r03-shipping-address-to-fulfilling-merchant")
	if p.DefinitionDigest == old.DefinitionDigest {
		t.Error("the reworded pack kept 0.3.8's digest")
	}
}

// 0.1.6 is 0.1.5 reworded (capsule-engine#182): only s01's statement changed.
func TestSeller016IsSeller015ReWorded(t *testing.T) {
	p, err := LoadSeller016()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	samePackBut(t, p, old, nil, "s01-personal-data-to-the-buyer-or-the-user")
	if p.DefinitionDigest == old.DefinitionDigest {
		t.Error("the reworded pack kept 0.1.5's digest")
	}
}

// One platform definition in both packs, and no selector or class name in
// either pack's statements' rules for it.
func TestThePlatformIsDefinedOnce(t *testing.T) {
	const want = "the marketplace, booking site or mail relay between you and the other party"
	for _, c := range []struct {
		load func() (Pack, error)
		rule string
	}{
		{LoadEveryday039, "r03-shipping-address-to-fulfilling-merchant"},
		{LoadSeller016, "s01-personal-data-to-the-buyer-or-the-user"},
	} {
		p, err := c.load()
		if err != nil {
			t.Fatal(err)
		}
		for _, r := range p.Rules {
			if r.ID != c.rule {
				continue
			}
			if !strings.Contains(strings.Join(strings.Fields(r.Statement), " "), want) {
				t.Errorf("%s %s: %q", p.ID, r.ID, r.Statement)
			}
		}
	}
	p, err := LoadEveryday039()
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range p.Rules {
		if r.ID == "r03-shipping-address-to-fulfilling-merchant" &&
			!strings.HasSuffix(strings.Join(strings.Fields(r.Statement), " "), "Your details go only to the merchant filling your order, a listing platform, or you.") {
			t.Errorf("r03 statement %q", r.Statement)
		}
	}
}
