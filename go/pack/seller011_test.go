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

// asg/seller/0.1.1 is vendored from capsule-engine #125's merge at the
// definition digest the engine computed there, beside 0.1.0, which still
// loads at its own.
const (
	seller011SourceCommit = "21975ce45837cb11418e2ea931d531d6dfd61979"
	seller011Digest       = "65e41f7e9a2a9cffc0f71027f8e7c5b70f3ccf2751e6cb95ba1c81649b41e1ac"
)

func TestSeller011IsVendoredFromThePin(t *testing.T) {
	data, err := fs.ReadFile(packs.Seller011, path.Join(packs.Seller011Dir, "PROVENANCE.json"))
	if err != nil {
		t.Fatal(err)
	}
	var prov struct {
		provenance
		SourceCommit string `json:"source_commit"`
	}
	if err := json.Unmarshal(data, &prov); err != nil {
		t.Fatal(err)
	}
	if prov.SourceCommit != seller011SourceCommit || prov.EngineDefinitionDigest != seller011Digest || prov.PackID != "asg/seller/0.1.1" {
		t.Fatalf("PROVENANCE names %s %s @ %s", prov.PackID, prov.SourceCommit, prov.EngineDefinitionDigest)
	}
	var onDisk, recorded []string
	err = fs.WalkDir(packs.Seller011, packs.Seller011Dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Name() == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p[len(packs.Seller011Dir)+1:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		b, err := fs.ReadFile(packs.Seller011, path.Join(packs.Seller011Dir, name))
		if err != nil {
			t.Fatal(err)
		}
		if sha256Hex(b) != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, sha256Hex(b), f.SHA256)
		}
	}
	slices.Sort(onDisk)
	if slices.Sort(recorded); !slices.Equal(onDisk, recorded) {
		t.Fatalf("vendored %v, provenance records %v", onDisk, recorded)
	}
	p, err := LoadSeller011()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := identityOf(p), (identity{"asg/seller/0.1.1", seller011Digest}); got != want {
		t.Fatalf("Go %+v, engine %+v", got, want)
	}
	for _, w := range p.Wickets {
		if prov.WicketDigests[w.ID] != w.Digest {
			t.Errorf("wicket %s: Go %s, engine %s", w.ID, w.Digest, prov.WicketDigests[w.ID])
		}
	}
	if len(p.Wickets) != len(prov.WicketDigests) || len(p.Rules) != 12 {
		t.Fatalf("%d wickets (engine %d), %d rules", len(p.Wickets), len(prov.WicketDigests), len(p.Rules))
	}
	// 0.1.0 is frozen and still loads at its own digest.
	if old, err := LoadSeller010(); err != nil || old.DefinitionDigest != sellerDigest {
		t.Fatalf("0.1.0: %v %s", err, old.DefinitionDigest)
	}
}

// An edited copy of asg/seller/0.1.1 is refused by its pin, as 0.1.0's is.
func TestEditedSeller011FailsThePin(t *testing.T) {
	dir := copyPackDir(t, packs.Seller011, packs.Seller011Dir, "pack.yaml", replace("No approval clears it.", "An approval clears it."))
	_, err := LoadPath(dir, builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) || pe.PackID != "asg/seller/0.1.1" || pe.Pinned != seller011Digest || pe.Digest == seller011Digest {
		t.Fatalf("edited 0.1.1: %v", err)
	}
}
