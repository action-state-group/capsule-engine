package pack

import (
	"bytes"
	"errors"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

func vendoredPackYAML(t *testing.T) []byte {
	t.Helper()
	b, err := fs.ReadFile(packs.Everyday038, path.Join(packs.Everyday038Dir, "pack.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// everyday037PackYAML is the frozen asg/everyday/0.3.7 pack.yaml: the
// engine digests of its edited copies were computed at 7339028.
func everyday037PackYAML(t *testing.T) []byte {
	t.Helper()
	b, err := fs.ReadFile(packs.Everyday037, path.Join(packs.Everyday037Dir, "pack.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// writePack writes a pack.yaml into a fresh directory and returns it.
func writePack(t *testing.T, data []byte) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "pack.yaml"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

// The vendored pack through the new entry points has the engine's digest,
// from the embedded copy and from a byte-identical copy on disk.
func TestLoadFromAndLoadPathGiveTheVendoredDigest(t *testing.T) {
	want := readEverydayProvenance(t).EngineDefinitionDigest
	fromFS, err := LoadFrom(packs.Everyday038, packs.Everyday038Dir)
	if err != nil {
		t.Fatal(err)
	}
	fromDisk, err := LoadPath(writePack(t, vendoredPackYAML(t)), builtInCatalog)
	if err != nil {
		t.Fatal(err)
	}
	if fromFS.DefinitionDigest != want || fromDisk.DefinitionDigest != want {
		t.Fatalf("LoadFrom %s, LoadPath %s, engine %s", fromFS.DefinitionDigest, fromDisk.DefinitionDigest, want)
	}
}

func TestLoadPathLoadsTheTestFixturePack(t *testing.T) {
	p, err := LoadPath(fixtureDir, builtInCatalog)
	if err != nil {
		t.Fatal(err)
	}
	if p.ID != "test/everyday-25-fixture/0.0.0" || len(p.Rules) != 25 {
		t.Fatalf("loaded %s with %d rules", p.ID, len(p.Rules))
	}
}

// A copy of asg/everyday/0.3.8 with one edited byte is refused, and the
// error names the pack_id, the digest the copy has, and the pinned digest.
func TestEditedCopyOfAPinnedPackIsRefused(t *testing.T) {
	pinned := readEverydayProvenance(t).EngineDefinitionDigest
	edited := bytes.Replace(vendoredPackYAML(t), []byte("Checks each payment"), []byte("Checks each paymenT"), 1)
	if bytes.Equal(edited, vendoredPackYAML(t)) {
		t.Fatal("edit did not apply")
	}
	unpinned, err := Load(edited, packs.Everyday038, packs.Everyday038Dir)
	if err != nil {
		t.Fatal(err)
	}
	_, err = LoadPath(writePack(t, edited), builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) {
		t.Fatalf("an edited copy of asg/everyday/0.3.8: %v", err)
	}
	if want := (PinnedDigestError{PackID: "asg/everyday/0.3.8", Digest: unpinned.DefinitionDigest, Pinned: pinned}); *pe != want {
		t.Fatalf("refusal names %+v, expected %+v", *pe, want)
	}
}

// An edited wicket citation is refused by the citation guard, naming the
// cited and the recomputed digest.
func TestEditedCitationIsRefused(t *testing.T) {
	edited := bytes.Replace(vendoredPackYAML(t), []byte("18ab5d489f1e5774"), []byte("28ab5d489f1e5774"), 1)
	_, err := LoadPath(writePack(t, edited), builtInCatalog)
	var ce *CitationDigestError
	if !errors.As(err, &ce) {
		t.Fatalf("edited citation: %v", err)
	}
	if want := (CitationDigestError{PackID: "asg/everyday/0.3.8", Field: "constraints[1]", Ref: "dedupe/1.0.0",
		Cited:    "28ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb",
		Computed: "18ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb"}); *ce != want {
		t.Fatalf("refusal names %+v, expected %+v", *ce, want)
	}
}

func TestLoadPathWithoutAPack(t *testing.T) {
	if _, err := LoadPath(t.TempDir(), builtInCatalog); err == nil {
		t.Fatal("loaded a directory with no pack.yaml")
	}
}

// copyVendoredPack copies the whole vendored pack directory (pack.yaml,
// wickets/, folds/) to disk, applying edit to one file.
func copyVendoredPack(t *testing.T, editFile string, edit func([]byte) []byte) string {
	t.Helper()
	return copyPackDir(t, packs.Everyday038, packs.Everyday038Dir, editFile, edit)
}

// copyPackDir is copyVendoredPack for the pack in dir of fsys.
func copyPackDir(t *testing.T, fsys fs.FS, dir, editFile string, edit func([]byte) []byte) string {
	t.Helper()
	dst := t.TempDir()
	err := fs.WalkDir(fsys, dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel := strings.TrimPrefix(strings.TrimPrefix(p, dir), "/")
		target := filepath.Join(dst, filepath.FromSlash(rel))
		if d.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		b, err := fs.ReadFile(fsys, p)
		if err != nil {
			return err
		}
		if rel == editFile {
			edited := edit(b)
			if bytes.Equal(edited, b) {
				t.Fatalf("edit to %s did not apply", rel)
			}
			b = edited
		}
		return os.WriteFile(target, b, 0o600)
	})
	if err != nil {
		t.Fatal(err)
	}
	return dst
}

func replace(old, new string) func([]byte) []byte {
	return func(b []byte) []byte { return bytes.Replace(b, []byte(old), []byte(new), 1) }
}

// An unedited full copy resolves against its own wickets/ and folds/ and has
// the vendored digest.
func TestFullCopyResolvesItsOwnCatalog(t *testing.T) {
	p, err := LoadPath(copyVendoredPack(t, "", nil), builtInCatalog)
	if err != nil {
		t.Fatal(err)
	}
	if p.DefinitionDigest != readEverydayProvenance(t).EngineDefinitionDigest {
		t.Fatalf("digest %s", p.DefinitionDigest)
	}
}

// An edited WICKET file in the pack source is read, digests differently, and
// fails the pack's own citation of it.
func TestEditedWicketFileIsRefused(t *testing.T) {
	dir := copyVendoredPack(t, "wickets/caps.v5.1.yaml", replace("money.transfer: 10000", "money.transfer: 10001"))
	edited, err := loadWickets(os.DirFS(dir), "wickets")
	if err != nil {
		t.Fatal(err)
	}
	_, err = LoadPath(dir, builtInCatalog)
	var ce *CitationDigestError
	if !errors.As(err, &ce) {
		t.Fatalf("edited caps wicket: %v", err)
	}
	if want := (CitationDigestError{PackID: "asg/everyday/0.3.8", Field: "constraints[0]", Ref: "caps/5.1.0",
		Cited: "7fd8def459eb42af32878108626748a76faea2bdf4f402530445f807ac5df2a8", Computed: edited["caps/5.1.0"].digest}); *ce != want {
		t.Fatalf("refusal names %+v, expected %+v", *ce, want)
	}
}

// An edited wicket whose citation is edited to match makes a different pack,
// which the asg/everyday/0.3.8 pin refuses.
func TestEditedWicketWithMatchingCitationFailsThePin(t *testing.T) {
	dir := copyVendoredPack(t, "wickets/caps.v5.1.yaml", replace("money.transfer: 10000", "money.transfer: 10001"))
	edited, err := loadWickets(os.DirFS(dir), "wickets")
	if err != nil {
		t.Fatal(err)
	}
	packYAML := filepath.Join(dir, "pack.yaml")
	b, err := os.ReadFile(packYAML)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(packYAML, bytes.Replace(b, []byte("7fd8def459eb42af32878108626748a76faea2bdf4f402530445f807ac5df2a8"), []byte(edited["caps/5.1.0"].digest), 1), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err = LoadPath(dir, builtInCatalog)
	var pe *PinnedDigestError
	if !errors.As(err, &pe) || pe.PackID != "asg/everyday/0.3.8" || pe.Pinned != readEverydayProvenance(t).EngineDefinitionDigest || pe.Digest == pe.Pinned {
		t.Fatalf("re-cited edited wicket: %v", err)
	}
}

// An edited FOLD file in the pack source is read, digests differently, and
// fails the pack's own citation of it.
func TestEditedFoldFileIsRefused(t *testing.T) {
	dir := copyVendoredPack(t, "folds/spend.weekly.v3.1.yaml", replace("duration: 7d", "duration: 8d"))
	edited, err := loadFolds(os.DirFS(dir), "folds")
	if err != nil {
		t.Fatal(err)
	}
	_, err = LoadPath(dir, builtInCatalog)
	var ce *CitationDigestError
	if !errors.As(err, &ce) {
		t.Fatalf("edited fold: %v", err)
	}
	if want := (CitationDigestError{PackID: "asg/everyday/0.3.8", Field: "folds[0]", Ref: "spend.weekly/3.1.0",
		Cited: "f379cd4b2b12284fa5d682845f23bcd7ab1ae8b8d1dd827cd9b5dd7069735997", Computed: edited["spend.weekly/3.1.0"].Digest}); *ce != want {
		t.Fatalf("refusal names %+v, expected %+v", *ce, want)
	}
}

// builtInCatalog is the catalog a pack.yaml-only source resolves against in
// these tests: asg/everyday/0.3.8's definitions.
var builtInCatalog = Catalog{FS: packs.Everyday038, Dir: packs.Everyday038Dir}

// Without a fallback catalog, a pack.yaml-only source's citations resolve
// against nothing.
func TestAPackYAMLAloneNeedsACatalog(t *testing.T) {
	dir := writePack(t, vendoredPackYAML(t))
	if _, err := LoadPath(dir, Catalog{}); err == nil {
		t.Fatal("a pack.yaml-only source with no catalog loaded")
	}
	if _, err := LoadPath(dir, builtInCatalog); err != nil {
		t.Fatal(err)
	}
}
