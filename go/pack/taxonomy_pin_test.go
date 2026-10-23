package pack

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// asg/everyday/0.3.7 pins the action taxonomy (capsule-engine packs/loader.py
// _parse_taxonomy_pin, 47c58cd): version 6 at the SHA-256 over its JCS bytes,
// the vendored packs/engine/action_taxonomy.json.
func TestShippedPackPinsTheVendoredTaxonomy(t *testing.T) {
	p, err := LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	version, digest, err := VendoredTaxonomy()
	if err != nil {
		t.Fatal(err)
	}
	want := TaxonomyPin{Version: "6", Digest: "fc12eb90bc2b3c85bb49c4d3d7be8f384c6e0b8e189bc3b68a753a4f1af5cd94"}
	if p.Taxonomy == nil || *p.Taxonomy != want || version != want.Version || digest != want.Digest {
		t.Fatalf("pack pins %+v; vendored taxonomy %s %s; want %+v", p.Taxonomy, version, digest, want)
	}
}

// The pin is inside the definition digest: the same pack without it digests
// to what capsule-engine computes for that edit at 7339028, not to the pin.
func TestTheTaxonomyPinIsDigestCovered(t *testing.T) {
	const engineDigest = "70e850ae7bc561d198710be4d94d023cfc97686bbd13469c45478a16cfd02db7"
	y := string(vendoredPackYAML(t))
	i := strings.Index(y, "\n# The action taxonomy this pack was authored against.")
	j := strings.Index(y, "\nbootstrap:")
	if i < 0 || j < i {
		t.Fatal("the vendored pack.yaml has no taxonomy block before bootstrap")
	}
	p, err := Load([]byte(y[:i]+"\n"+y[j:]), packs.Everyday037, packs.Everyday037Dir)
	if err != nil {
		t.Fatal(err)
	}
	if p.Taxonomy != nil || p.DefinitionDigest != engineDigest {
		t.Fatalf("without its pin the pack digests to %s (pin %+v), the engine to %s", p.DefinitionDigest, p.Taxonomy, engineDigest)
	}
}

// A pin naming any other taxonomy is refused, as the engine refuses it
// (taxonomy_pin_mismatch), naming both versions and both digests.
func TestAMismatchedTaxonomyPinIsRefused(t *testing.T) {
	for name, edit := range map[string][2]string{
		"version": {`taxonomy_version: "6"`, `taxonomy_version: "5"`},
		"digest":  {`digest: "fc12eb90`, `digest: "0c12eb90`},
	} {
		_, err := loadEdited(t, edit[0], edit[1])
		var te *TaxonomyPinError
		if !errors.As(err, &te) || te.VendoredVersion != "6" || te.VendoredDigest != "fc12eb90bc2b3c85bb49c4d3d7be8f384c6e0b8e189bc3b68a753a4f1af5cd94" {
			t.Errorf("%s: %v, want a taxonomy pin refusal", name, err)
		}
	}
	// A pin without a digest is incomplete, not a mismatch.
	_, err := loadEdited(t, `  digest: "fc12eb90bc2b3c85bb49c4d3d7be8f384c6e0b8e189bc3b68a753a4f1af5cd94"`+"\n", "")
	var te *TaxonomyPinError
	if err == nil || errors.As(err, &te) {
		t.Errorf("a pin without a digest: %v", err)
	}
}

// A wicket definition's semantics is inside its digest (capsule-engine
// guards/wickets/definition.py, 47c58cd): upfront_amount/1.1.0 without it
// digests to what the engine computes for that edit, so the pack's citation
// no longer matches.
func TestWicketSemanticsIsDigestCovered(t *testing.T) {
	const engineDigest = "12b17dad0332d4e99d772930db2e802c9d8846ed9677e3747b3adf13882a832c"
	dir := copyVendoredPack(t, "wickets/upfront_amount.v1.1.yaml", func(b []byte) []byte {
		i, j := bytes.Index(b, []byte("semantics: >-")), bytes.Index(b, []byte("config:"))
		if i < 0 || j < i {
			t.Fatal("upfront_amount.v1.1.yaml has no semantics before config")
		}
		return append(b[:i:i], b[j:]...)
	})
	_, err := LoadPath(dir, builtInCatalog)
	var ce *CitationDigestError
	if !errors.As(err, &ce) || ce.Ref != "upfront_amount/1.1.0" || ce.Computed != engineDigest {
		t.Fatalf("upfront_amount/1.1.0 without semantics: %v, want it to digest to %s", err, engineDigest)
	}
}
