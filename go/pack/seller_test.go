package pack

import (
	"encoding/json"
	"errors"
	"io/fs"
	"path"
	"slices"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// The seller pack is vendored from capsule-engine #122's merge, at the
// definition digest the engine computed there.
const (
	sellerSourceCommit = "ba76a97a0fb5b41333b1b76a489060221e51ea45"
	sellerDigest       = "7b854f0f0feb51381d936ad992bef659234d77a799a2a319585a856f65a19c77"
)

func readSellerProvenance(t *testing.T) (provenance, string) {
	t.Helper()
	data, err := fs.ReadFile(packs.Seller010, path.Join(packs.Seller010Dir, "PROVENANCE.json"))
	if err != nil {
		t.Fatal(err)
	}
	var p provenance
	if err := json.Unmarshal(data, &p); err != nil {
		t.Fatal(err)
	}
	var src struct {
		SourceCommit string `json:"source_commit"`
	}
	if err := json.Unmarshal(data, &src); err != nil {
		t.Fatal(err)
	}
	return p, src.SourceCommit
}

// Every vendored seller file is the engine's bytes, and every file in the
// directory is one PROVENANCE names: nothing is vendored unrecorded.
func TestSellerVendoredBytesMatchProvenance(t *testing.T) {
	prov, _ := readSellerProvenance(t)
	var onDisk []string
	err := fs.WalkDir(packs.Seller010, packs.Seller010Dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Name() == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p[len(packs.Seller010Dir)+1:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	slices.Sort(onDisk)
	var recorded []string
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		data, err := fs.ReadFile(packs.Seller010, path.Join(packs.Seller010Dir, name))
		if err != nil {
			t.Fatal(err)
		}
		if sha256Hex(data) != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, sha256Hex(data), f.SHA256)
		}
	}
	if slices.Sort(recorded); !slices.Equal(onDisk, recorded) {
		t.Fatalf("vendored %v, provenance records %v", onDisk, recorded)
	}
}

// Cross-implementation: the Go loader reproduces the digest capsule-engine
// computed for asg/seller/0.1.0 at the vendored commit.
func TestSellerIsVendoredFromThePin(t *testing.T) {
	prov, commit := readSellerProvenance(t)
	if commit != sellerSourceCommit || prov.EngineDefinitionDigest != sellerDigest || prov.PackID != "asg/seller/0.1.0" {
		t.Fatalf("PROVENANCE names %s %s @ %s, want asg/seller/0.1.0 %s @ %s", prov.PackID, commit, prov.EngineDefinitionDigest, sellerSourceCommit, sellerDigest)
	}
	p, err := LoadSeller010()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := identityOf(p), (identity{"asg/seller/0.1.0", sellerDigest}); got != want {
		t.Fatalf("Go %+v, engine %+v", got, want)
	}
	if len(p.Rules) != 12 {
		t.Fatalf("%d rules, the pack declares 12", len(p.Rules))
	}
}

// Every vendored seller wicket and fold digests in Go to what the engine
// computed for it, and every one is cited by the pack.
func TestSellerDefinitionDigestsMatchEngine(t *testing.T) {
	prov, _ := readSellerProvenance(t)
	ws, err := loadWickets(packs.Seller010, path.Join(packs.Seller010Dir, "wickets"))
	if err != nil {
		t.Fatal(err)
	}
	if len(ws) != len(prov.WicketDigests) {
		t.Fatalf("%d wickets vendored, engine digested %d", len(ws), len(prov.WicketDigests))
	}
	for id, d := range prov.WicketDigests {
		if ws[id].digest != d {
			t.Errorf("wicket %s: Go %s, engine %s", id, ws[id].digest, d)
		}
	}
	folds, err := loadFolds(packs.Seller010, path.Join(packs.Seller010Dir, "folds"))
	if err != nil {
		t.Fatal(err)
	}
	if len(folds) != len(prov.FoldDigests) {
		t.Fatalf("%d folds vendored, provenance records %d", len(folds), len(prov.FoldDigests))
	}
	for id, d := range prov.FoldDigests {
		if folds[id].Digest != d {
			t.Errorf("fold %s: Go %s, recorded %s", id, folds[id].Digest, d)
		}
	}
	p, err := LoadSeller010()
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Wickets) != len(ws) || len(p.Folds) != len(folds) {
		t.Fatalf("the pack cites %d wickets and %d folds; %d and %d are vendored", len(p.Wickets), len(p.Folds), len(ws), len(folds))
	}
}

// The seller pack pins taxonomy 6, and the pin is exactly the vendored
// taxonomy everyday pins: one taxonomy for both packs.
func TestSellerPinsTheVendoredTaxonomy(t *testing.T) {
	p, err := LoadSeller010()
	if err != nil {
		t.Fatal(err)
	}
	version, digest, err := VendoredTaxonomy()
	if err != nil {
		t.Fatal(err)
	}
	if p.Taxonomy == nil || *p.Taxonomy != (TaxonomyPin{Version: "6", Digest: digest}) || version != "6" {
		t.Fatalf("seller pins %+v; vendored taxonomy is version %s digest %s", p.Taxonomy, version, digest)
	}
	e, err := LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	if *e.Taxonomy != *p.Taxonomy {
		t.Fatalf("everyday pins %+v, seller %+v", *e.Taxonomy, *p.Taxonomy)
	}
}

// An edited copy of the seller pack is not asg/seller/0.1.0: the pin
// refuses it rather than load it under that name.
func TestEditedSellerPackFailsThePin(t *testing.T) {
	dir := copyPackDir(t, packs.Seller010, packs.Seller010Dir, "pack.yaml", replace("card and p2p)", "card)"))
	_, err := LoadPath(dir, builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) || pe.PackID != "asg/seller/0.1.0" || pe.Pinned != sellerDigest || pe.Digest == sellerDigest {
		t.Fatalf("edited seller pack: %v", err)
	}
}

// An edited seller wicket fails the pack's citation of it.
func TestEditedSellerWicketIsRefused(t *testing.T) {
	dir := copyPackDir(t, packs.Seller010, packs.Seller010Dir, "wickets/price_floor.yaml", replace("marketplace.offer", "marketplace.order"))
	_, err := LoadPath(dir, builtInCatalog)
	var ce *CitationDigestError
	if !errors.As(err, &ce) || ce.Ref != "price_floor/2.0.1" {
		t.Fatalf("edited price_floor wicket: %v", err)
	}
	// The unedited copy loads at the pin, so the refusal is the edit's.
	if p, err := LoadPath(copyPackDir(t, packs.Seller010, packs.Seller010Dir, "", nil), builtInCatalog); err != nil || p.DefinitionDigest != sellerDigest {
		t.Fatalf("unedited copy: %v %s", err, p.DefinitionDigest)
	}
}

// The known party roles are capsule-cli's: absent, buyer and seller. Any
// other is an UnknownPartyRoleError naming it.
func TestKnownPartyRoles(t *testing.T) {
	for _, role := range []string{"", PartyBuyer, PartySeller} {
		if !KnownPartyRole(role) {
			t.Errorf("party_role %q is not known", role)
		}
	}
	for _, role := range []string{"broker", "Seller", " seller"} {
		if KnownPartyRole(role) {
			t.Errorf("party_role %q is known", role)
		}
		want := `party_role "` + role + `" is not one this plugin knows (buyer, seller)`
		if got := (&UnknownPartyRoleError{Role: role}).Error(); got != want {
			t.Errorf("%q: %s, want %s", role, got, want)
		}
	}
}
