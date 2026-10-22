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

// asg/seller/0.1.3 is vendored from capsule-engine 08cd96d (the merge of
// #139) at the definition digest the engine computes there, beside 0.1.0 to
// 0.1.2, which still load at their own. The wicket and fold it adds are
// pinned at the engine's digests, each checked in full.
const (
	seller013SourceCommit      = "08cd96d03b6b23e0a7b71169dd5c24ea0381ff04"
	seller013Digest            = "f0068688317b0cae3c40e839d7cff0137991aca466a878f9b558356f1808321a"
	requiredDisclosure11Digest = "80f3af6e428f2331f6ab458997d996e172b9fc2e721b55fcf473f4e31d1492c0"
	statementSeenDigest        = "925e7ccd2e29b3f1984f966e968bdef47d0d22d2b85b8e0205a39d6a5a7ff0be"
	requiredDisclosure10Digest = "c8f216415be1fac673d1efc6df2e7a9666b5d5c8e639fbb3d8379a24bb8f5ebf"
	requiredStatementRuleID    = "s05-required-statement-made-first"
)

func TestSeller013IsVendoredFromThePin(t *testing.T) {
	data, err := fs.ReadFile(packs.Seller013, path.Join(packs.Seller013Dir, "PROVENANCE.json"))
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
	if prov.SourceCommit != seller013SourceCommit || prov.EngineDefinitionDigest != seller013Digest || prov.PackID != "asg/seller/0.1.3" {
		t.Fatalf("PROVENANCE names %s %s @ %s", prov.PackID, prov.SourceCommit, prov.EngineDefinitionDigest)
	}
	var onDisk, recorded []string
	err = fs.WalkDir(packs.Seller013, packs.Seller013Dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Name() == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p[len(packs.Seller013Dir)+1:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		b, err := fs.ReadFile(packs.Seller013, path.Join(packs.Seller013Dir, name))
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
	p, err := LoadSeller013()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := identityOf(p), (identity{"asg/seller/0.1.3", seller013Digest}); got != want {
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
	if w, ok := p.Wicket("required_disclosure"); !ok || w.ID != "required_disclosure/1.1.0" || w.Digest != requiredDisclosure11Digest {
		t.Fatalf("required_disclosure: %+v", w)
	}
	if f, ok := p.Fold("disclosure.statement_seen/1.0.0"); !ok || f.Digest != statementSeenDigest {
		t.Fatalf("disclosure.statement_seen/1.0.0: %+v", f)
	}
	// 0.1.0 to 0.1.2 are frozen and still load at their own digests.
	if old, err := LoadSeller010(); err != nil || old.DefinitionDigest != sellerDigest {
		t.Fatalf("0.1.0: %v %s", err, old.DefinitionDigest)
	}
	if old, err := LoadSeller011(); err != nil || old.DefinitionDigest != seller011Digest {
		t.Fatalf("0.1.1: %v %s", err, old.DefinitionDigest)
	}
	if old, err := LoadSeller012(); err != nil || old.DefinitionDigest != seller012Digest {
		t.Fatalf("0.1.2: %v %s", err, old.DefinitionDigest)
	}
}

// 0.1.3 is 0.1.2 with required_disclosure/1.1.0 in place of 1.0.0 and the
// fold disclosure.statement_seen/1.0.0 added: every other definition it
// cites is 0.1.2's, and only s05's statement changes among the rules.
func TestSeller013Is012WithStatements(t *testing.T) {
	p, err := LoadSeller013()
	if err != nil {
		t.Fatal(err)
	}
	old, err := LoadSeller012()
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Wickets) != len(old.Wickets) {
		t.Fatalf("0.1.3 cites %d wickets, 0.1.2 %d", len(p.Wickets), len(old.Wickets))
	}
	for i, w := range p.Wickets {
		o := old.Wickets[i]
		if o.ID == "required_disclosure/1.0.0" {
			if o.Digest != requiredDisclosure10Digest || w.ID != "required_disclosure/1.1.0" || w.Digest != requiredDisclosure11Digest {
				t.Errorf("wicket %d: 0.1.3 %s %s in place of 0.1.2 %s %s", i, w.ID, w.Digest, o.ID, o.Digest)
			}
			continue
		}
		if w.ID != o.ID || w.Digest != o.Digest {
			t.Errorf("wicket %d: 0.1.3 %s %s, 0.1.2 %s %s", i, w.ID, w.Digest, o.ID, o.Digest)
		}
	}
	if len(p.Folds) != len(old.Folds)+1 {
		t.Errorf("0.1.3 cites %d folds, 0.1.2 %d", len(p.Folds), len(old.Folds))
	}
	for _, f := range old.Folds {
		if g, ok := p.Fold(f.ID); !ok || g.Digest != f.Digest {
			t.Errorf("fold %s: 0.1.3 %+v, 0.1.2 %s", f.ID, g, f.Digest)
		}
	}
	if len(p.Rules) != len(old.Rules) {
		t.Fatalf("0.1.3 %d rules, 0.1.2 %d", len(p.Rules), len(old.Rules))
	}
	for i, r := range p.Rules {
		o := old.Rules[i]
		if r.ID == requiredStatementRuleID && r.Statement != o.Statement {
			r.Statement = o.Statement
		}
		if r != o {
			t.Errorf("rule %d differs:\n0.1.3 %+v\n0.1.2 %+v", i, r, o)
		}
	}
}

// An edited copy of asg/seller/0.1.3 is refused by its pin.
func TestEditedSeller013FailsThePin(t *testing.T) {
	dir := copyPackDir(t, packs.Seller013, packs.Seller013Dir, "pack.yaml", replace("No approval clears it.", "An approval clears it."))
	_, err := LoadPath(dir, builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) || pe.PackID != "asg/seller/0.1.3" || pe.Pinned != seller013Digest || pe.Digest == seller013Digest {
		t.Fatalf("edited 0.1.3: %v", err)
	}
}
