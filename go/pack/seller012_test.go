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

// asg/seller/0.1.2 is vendored from capsule-engine e0e664d (merged by #128
// as 2ba51d4 with the same pack bytes) at the definition digest the engine
// computes there, beside 0.1.0 and 0.1.1, which still load at their own.
const (
	seller012SourceCommit = "e0e664de1ede4febae31931579ebe85497b4b120"
	seller012Digest       = "e599c5aff8d4171d5e58cae33fdc7022e4ee3762d1a884bb6e6098aca532a353"
)

func TestSeller012IsVendoredFromThePin(t *testing.T) {
	data, err := fs.ReadFile(packs.Seller012, path.Join(packs.Seller012Dir, "PROVENANCE.json"))
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
	if prov.SourceCommit != seller012SourceCommit || prov.EngineDefinitionDigest != seller012Digest || prov.PackID != "asg/seller/0.1.2" {
		t.Fatalf("PROVENANCE names %s %s @ %s", prov.PackID, prov.SourceCommit, prov.EngineDefinitionDigest)
	}
	var onDisk, recorded []string
	err = fs.WalkDir(packs.Seller012, packs.Seller012Dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Name() == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p[len(packs.Seller012Dir)+1:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		b, err := fs.ReadFile(packs.Seller012, path.Join(packs.Seller012Dir, name))
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
	p, err := LoadSeller012()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := identityOf(p), (identity{"asg/seller/0.1.2", seller012Digest}); got != want {
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
	// 0.1.0 and 0.1.1 are frozen and still load at their own digests.
	if old, err := LoadSeller010(); err != nil || old.DefinitionDigest != sellerDigest {
		t.Fatalf("0.1.0: %v %s", err, old.DefinitionDigest)
	}
	if old, err := LoadSeller011(); err != nil || old.DefinitionDigest != seller011Digest {
		t.Fatalf("0.1.1: %v %s", err, old.DefinitionDigest)
	}
}

// 0.1.2 is 0.1.1 without caps/1.0.0 and its fold spend.weekly/1.0.0: every
// other definition it cites, and every rule, is 0.1.1's.
func TestSeller012Is011WithoutCaps(t *testing.T) {
	p, err := LoadSeller012()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadSeller011()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := p.Wicket("caps"); ok {
		t.Error("0.1.2 cites a caps definition")
	}
	if _, ok := p.Fold("spend.weekly/1.0.0"); ok {
		t.Error("0.1.2 cites spend.weekly/1.0.0")
	}
	var want []Wicket
	for _, w := range old.Wickets {
		if w.Check != "caps" {
			want = append(want, w)
		}
	}
	if len(p.Wickets) != len(want) {
		t.Fatalf("0.1.2 cites %d wickets, 0.1.1 less caps %d", len(p.Wickets), len(want))
	}
	for i, w := range p.Wickets {
		if w.ID != want[i].ID || w.Digest != want[i].Digest {
			t.Errorf("wicket %d: 0.1.2 %s %s, 0.1.1 %s %s", i, w.ID, w.Digest, want[i].ID, want[i].Digest)
		}
	}
	if len(p.Folds) != len(old.Folds)-1 {
		t.Errorf("0.1.2 cites %d folds, 0.1.1 %d", len(p.Folds), len(old.Folds))
	}
	if !slices.Equal(p.Rules, old.Rules) {
		t.Errorf("rules differ:\n0.1.2 %+v\n0.1.1 %+v", p.Rules, old.Rules)
	}
}

// An edited copy of asg/seller/0.1.2 is refused by its pin, as 0.1.1's is.
func TestEditedSeller012FailsThePin(t *testing.T) {
	dir := copyPackDir(t, packs.Seller012, packs.Seller012Dir, "pack.yaml", replace("No approval clears it.", "An approval clears it."))
	_, err := LoadPath(dir, builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) || pe.PackID != "asg/seller/0.1.2" || pe.Pinned != seller012Digest || pe.Digest == seller012Digest {
		t.Fatalf("edited 0.1.2: %v", err)
	}
}
