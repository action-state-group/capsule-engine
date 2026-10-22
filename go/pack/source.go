package pack

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path"
)

// CitationDigestError is a pack citing a wicket or fold definition at a
// digest the vendored definition does not produce.
type CitationDigestError struct {
	PackID, Field, Ref, Cited, Computed string
}

func (e *CitationDigestError) Error() string {
	return fmt.Sprintf("pack %s: %s cites %q at digest %s, but the definition digests to %s", e.PackID, e.Field, e.Ref, e.Cited, e.Computed)
}

// PinnedDigestError is a pack naming a vendored pack_id whose definition
// digest is not that pack's pinned digest.
type PinnedDigestError struct {
	PackID, Digest, Pinned string
}

func (e *PinnedDigestError) Error() string {
	return fmt.Sprintf("pack %s digests to %s, but %s is pinned at %s: a changed pack is not that pack and needs its own pack_id",
		e.PackID, e.Digest, e.PackID, e.Pinned)
}

// retiredPins are the everyday pack ids this repository shipped before, at
// the digests capsule-engine computed for them. A pack id names one content
// for good: a pack claiming a retired id at another digest is refused as
// firmly as an edited copy of the shipped one.
var retiredPins = map[string]string{
	"asg/everyday/0.1.0": "0b1e3946abc8e84dd3255e3be564c5cdf480bedfa82b8e0ac72d5554d6b79463",
	"asg/everyday/0.2.0": "61a3c8951ebf615a40de327061bdb602963947d42f104fdd346445c239bbfbdc",
	"asg/everyday/0.2.1": "9eb23761f94561fc7ee6bd440e76e78a018b4a331a26f6b24826e2f40d238453",
	"asg/everyday/0.3.1": "cf10d1af09bfca7dcec25e259702184306ccbaf1d7af5ebad8aa538202b22b55",
	"asg/everyday/0.3.2": "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2",
	"asg/everyday/0.3.3": "d153219b9b5f7ad8eb4805eb718aab81a5dfa29a6a1d977301559fc167268752",
}

// pinned is pack_id -> definition digest for every vendored pack, read from
// each one's PROVENANCE.json sidecar (the digest capsule-engine computed),
// and every retired one (retiredPins). A pack that names one of these ids
// must digest to exactly this value.
func pinned() (map[string]string, error) {
	pins := map[string]string{}
	for _, v := range vendored {
		b, err := fs.ReadFile(v.fsys, path.Join(v.dir, "PROVENANCE.json"))
		if err != nil {
			return nil, err
		}
		var prov struct {
			PackID                 string `json:"pack_id"`
			EngineDefinitionDigest string `json:"engine_definition_digest"`
		}
		if err := json.Unmarshal(b, &prov); err != nil {
			return nil, fmt.Errorf("%s PROVENANCE.json: %w", v.dir, err)
		}
		pins[prov.PackID] = prov.EngineDefinitionDigest
	}
	for id, digest := range retiredPins {
		if _, ok := pins[id]; !ok {
			pins[id] = digest
		}
	}
	return pins, nil
}

// Catalog is a directory of wicket and fold definitions (wickets/, folds/)
// that a pack's citations resolve against.
type Catalog struct {
	FS  fs.FS
	Dir string
}

// catalogFor is where a pack's cited wicket and fold definitions resolve. A
// pack source carrying its own wickets/ or folds/ directory is its own
// catalog: every definition it ships is read and digested, so an edited
// definition no longer matches the pack's citation (or, if the citation was
// edited too, the pack no longer matches its pin). A source holding only a
// pack.yaml resolves against fallback, the built-in catalog its caller names,
// as capsule-engine resolves against its own. With no fallback it resolves
// against nothing, so a citation does not resolve.
func catalogFor(packFS fs.FS, dir string, fallback Catalog) (fs.FS, string) {
	for _, sub := range []string{"wickets", "folds"} {
		if info, err := fs.Stat(packFS, path.Join(dir, sub)); err == nil && info.IsDir() {
			return packFS, dir
		}
	}
	if fallback.FS == nil {
		return packFS, dir
	}
	return fallback.FS, fallback.Dir
}

// LoadFrom loads the pack whose pack.yaml is in dir of packFS, resolving its
// citations against its own definitions, only at their exact digests. A
// pack naming a vendored pack_id must digest to that pack's pinned digest: an
// edited copy of asg/everyday/0.2.1 is not asg/everyday/0.2.1, and is refused
// rather than loaded under that name.
func LoadFrom(packFS fs.FS, dir string) (Pack, error) {
	return loadFrom(packFS, dir, dir, Catalog{})
}

// loadFrom is LoadFrom with the source named as label in errors.
func loadFrom(packFS fs.FS, dir, label string, fallback Catalog) (Pack, error) {
	data, err := fs.ReadFile(packFS, path.Join(dir, "pack.yaml"))
	if err != nil {
		return Pack{}, fmt.Errorf("pack: %w", err)
	}
	catalogFS, catalogDir := catalogFor(packFS, dir, fallback)
	p, err := Load(data, catalogFS, catalogDir)
	if err != nil {
		return Pack{}, fmt.Errorf("pack in %s: %w", label, err)
	}
	pins, err := pinned()
	if err != nil {
		return Pack{}, err
	}
	if want, ok := pins[p.ID]; ok && want != p.DefinitionDigest {
		return Pack{}, &PinnedDigestError{PackID: p.ID, Digest: p.DefinitionDigest, Pinned: want}
	}
	return p, nil
}

// LoadPath loads the pack in a directory on disk (its pack.yaml). A
// directory holding only a pack.yaml resolves its citations against
// fallback (catalogFor).
func LoadPath(dir string, fallback Catalog) (Pack, error) {
	return loadFrom(os.DirFS(dir), ".", dir, fallback)
}
