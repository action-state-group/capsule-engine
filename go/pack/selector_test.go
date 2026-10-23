package pack

import (
	"errors"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// loadEdited loads the vendored pack.yaml with one edit, against the
// vendored catalog.
func loadEdited(t *testing.T, old, new string) (Pack, error) {
	t.Helper()
	y := string(vendoredPackYAML(t))
	if !strings.Contains(y, old) {
		t.Fatalf("the vendored pack.yaml has no %q", old)
	}
	return Load([]byte(strings.Replace(y, old, new, 1)), packs.Everyday038, packs.Everyday038Dir)
}

// capsule-engine packs/loader.py _obligation_selector at a5e9e30: an
// obligation citing a check whose wicket has selectors names exactly one of
// them; one citing a check without selectors names none.
func TestObligationSelectorIsRequiredAndClosed(t *testing.T) {
	for name, c := range map[string]struct {
		old, new string
		want     SelectorKind
	}{
		"missing":                {"    selector: booking_create\n", "", SelectorMissing},
		"not one of the check's": {"selector: booking_create", "selector: booking_modify", SelectorNotOfCheck},
		"on a check without":     {"    check: caps\n", "    check: caps\n    selector: booking_create\n", SelectorOnCheckWithout},
	} {
		_, err := loadEdited(t, c.old, c.new)
		var se *SelectorError
		if !errors.As(err, &se) || se.Kind != c.want {
			t.Errorf("%s: %v, want a selector error of kind %d", name, err, c.want)
		}
	}
}

// The selector is part of the definition: asg/everyday/0.3.7 with r16
// measured by another selector digests to what capsule-engine computes for
// that edit at 7339028 (load_pack_dir on the edited copy), not to its pin.
func TestTheSelectorIsDigestCovered(t *testing.T) {
	const engineDigest = "f82ef8375dc152e04e0a56d107821b88c3240f076b64fb96739ca4cfd71e2261"
	y := string(everyday037PackYAML(t))
	p, err := Load([]byte(strings.Replace(y, "selector: booking_create", "selector: booking_cancel", 1)), packs.Everyday037, packs.Everyday037Dir)
	if err != nil {
		t.Fatal(err)
	}
	if p.DefinitionDigest != engineDigest || p.DefinitionDigest == "c1cea374102bba60de82e16663c550a278ee07a399c5faeb285fe1ea1ea27d4d" {
		t.Fatalf("r16 moved to booking_cancel digests to %s, the engine to %s", p.DefinitionDigest, engineDigest)
	}
	if r, _ := p.Rule("r16-booking-with-a-commitment"); r.Selector != "booking_cancel" {
		t.Fatalf("r16 selector %q", r.Selector)
	}
}

// A retired id stays pinned at the digest capsule-engine computed for it: a
// pack claiming that id at any other digest (here the shipped content under
// the old id) is refused, naming the id and both digests. 0.3.1 was retired
// with the 0.3.2 re-vendor (365eb92); 0.3.2 with the 0.3.3 one (a5e9e30's
// definition, unchanged to 8d7bff2); 0.3.3 with the 0.3.4 one (47c58cd's
// definition, the digest the 0.3.3 re-vendor shipped). 0.3.4 and 0.3.5 are
// not retired: they stay vendored, frozen (TestFrozen034IsPinned,
// TestFrozen035IsPinned).
func TestRetired031IsPinned(t *testing.T) {
	retiredIDIsPinned(t, "asg/everyday/0.3.1", "cf10d1af09bfca7dcec25e259702184306ccbaf1d7af5ebad8aa538202b22b55")
}

func TestRetired032IsPinned(t *testing.T) {
	retiredIDIsPinned(t, "asg/everyday/0.3.2", "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2")
}

func TestRetired033IsPinned(t *testing.T) {
	retiredIDIsPinned(t, "asg/everyday/0.3.3", "d153219b9b5f7ad8eb4805eb718aab81a5dfa29a6a1d977301559fc167268752")
}

// asg/everyday/0.3.4 stays vendored at the digest capsule-engine computed
// for it (97a2aff, and tests/fixtures/packs/everyday-0.3.4 at c878edf0): it
// loads there, and a pack claiming its id at any other digest is refused.
func TestFrozen034IsPinned(t *testing.T) {
	const digest = "cd98aaec5acf8cea86f91fc21dd7df6b36a67c7b55340489b66e6ce88b85117b"
	p, err := LoadEveryday034()
	if err != nil || p.ID != "asg/everyday/0.3.4" || p.DefinitionDigest != digest {
		t.Fatalf("the frozen 0.3.4: %s %s %v", p.ID, p.DefinitionDigest, err)
	}
	if w, ok := p.Wicket("counterparty_seen_before"); !ok || w.ID != "counterparty_seen_before/3.0.0" {
		t.Fatalf("the frozen 0.3.4 cites %s", w.ID)
	}
	retiredIDIsPinned(t, "asg/everyday/0.3.4", digest)
}

// asg/everyday/0.3.5 stays vendored at the digest capsule-engine computed
// for it (c878edf0, and tests/fixtures/packs/everyday-0.3.5 at a710636c): it
// loads there, citing caps/5.0.0 over spend.weekly/3.0.0, and a pack
// claiming its id at any other digest is refused.
func TestFrozen035IsPinned(t *testing.T) {
	const digest = "0400d22c4464e91728bac2d00ca3bf8551ec98cdd20ef18d803b5b4432d4e8f4"
	p, err := LoadEveryday035()
	if err != nil || p.ID != "asg/everyday/0.3.5" || p.DefinitionDigest != digest {
		t.Fatalf("the frozen 0.3.5: %s %s %v", p.ID, p.DefinitionDigest, err)
	}
	if w, ok := p.Wicket("caps"); !ok || w.ID != "caps/5.0.0" || len(p.Folds) != 2 || p.Folds[0].ID != "spend.weekly/3.0.0" {
		t.Fatalf("the frozen 0.3.5 cites %s over %+v", w.ID, p.Folds)
	}
	retiredIDIsPinned(t, "asg/everyday/0.3.5", digest)
}

func retiredIDIsPinned(t *testing.T, id, digest string) {
	t.Helper()
	y := strings.Replace(string(vendoredPackYAML(t)), "pack_id: asg/everyday/0.3.8", "pack_id: "+id, 1)
	_, err := LoadPath(writePack(t, []byte(y)), builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) || pe.PackID != id || pe.Pinned != digest || pe.Digest == pe.Pinned {
		t.Fatalf("a pack claiming %s at another digest: %v", id, err)
	}
}
