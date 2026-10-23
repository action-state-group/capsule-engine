package pack

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io/fs"
	"maps"
	"os"
	"path"
	"path/filepath"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

type provenance struct {
	PackID                 string `json:"pack_id"`
	EngineDefinitionDigest string `json:"engine_definition_digest"`
	Files                  map[string]struct {
		SHA256 string `json:"sha256"`
	} `json:"files"`
	WicketDigests map[string]string `json:"wicket_digests"`
	FoldDigests   map[string]string `json:"fold_digests"`
	CatalogOnly   struct {
		WicketDigests map[string]string `json:"wicket_digests"`
		FoldDigests   map[string]string `json:"fold_digests"`
	} `json:"catalog_only"`
}

type golden struct {
	PackID                 string `json:"pack_id"`
	PackYAMLSHA256         string `json:"pack_yaml_sha256"`
	EngineDefinitionDigest string `json:"engine_definition_digest"`
}

func readEverydayProvenance(t *testing.T) provenance {
	t.Helper()
	data, err := fs.ReadFile(packs.Everyday038, path.Join(packs.Everyday038Dir, "PROVENANCE.json"))
	if err != nil {
		t.Fatal(err)
	}
	var p provenance
	if err := json.Unmarshal(data, &p); err != nil {
		t.Fatal(err)
	}
	return p
}

const fixtureDir = "testdata/everyday-25-test-fixture"

func fixtureBytes(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(fixtureDir, "pack.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func loadFixture(t *testing.T, data []byte) (Pack, error) {
	t.Helper()
	return Load(data, packs.Everyday038, packs.Everyday038Dir)
}

// identity is what a golden pins: the pack id and its definition digest.
type identity struct {
	PackID, Digest string
}

func identityOf(p Pack) identity { return identity{p.ID, p.DefinitionDigest} }

func sha256Hex(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

// The vendored files are the engine's bytes, unchanged.
func TestVendoredBytesMatchProvenance(t *testing.T) {
	prov := readEverydayProvenance(t)
	for name, f := range prov.Files {
		data, err := fs.ReadFile(packs.Everyday038, path.Join(packs.Everyday038Dir, name))
		if err != nil {
			t.Fatal(err)
		}
		if sha256Hex(data) != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, sha256Hex(data), f.SHA256)
		}
	}
}

// Cross-implementation: the Go digest of the vendored pack equals the digest
// capsule-engine computed at the vendored commit.
func TestEverydayDigestMatchesEngineGolden(t *testing.T) {
	prov := readEverydayProvenance(t)
	p, err := LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := identityOf(p), (identity{prov.PackID, prov.EngineDefinitionDigest}); got != want {
		t.Fatalf("Go %+v, engine golden %+v", got, want)
	}
}

func TestWicketDigestsMatchEngine(t *testing.T) {
	prov := readEverydayProvenance(t)
	ws, err := loadWickets(packs.Everyday038, path.Join(packs.Everyday038Dir, "wickets"))
	if err != nil {
		t.Fatal(err)
	}
	all := maps.Clone(prov.WicketDigests)
	maps.Copy(all, prov.CatalogOnly.WicketDigests)
	if len(ws) != len(all) {
		t.Fatalf("%d wickets vendored, engine digested %d", len(ws), len(all))
	}
	for id, d := range all {
		if ws[id].digest != d {
			t.Errorf("wicket %s: Go %s, engine %s", id, ws[id].digest, d)
		}
	}
}

// Every vendored fold, cited or catalog-only, digests in Go to what
// capsule-engine computed for it (a reversal clause included).
func TestFoldDigestsMatchEngine(t *testing.T) {
	prov := readEverydayProvenance(t)
	fs, err := loadFolds(packs.Everyday038, path.Join(packs.Everyday038Dir, "folds"))
	if err != nil {
		t.Fatal(err)
	}
	all := maps.Clone(prov.FoldDigests)
	maps.Copy(all, prov.CatalogOnly.FoldDigests)
	if len(fs) != len(all) {
		t.Fatalf("%d folds vendored, engine digested %d", len(fs), len(all))
	}
	for id, d := range all {
		if fs[id].Digest != d {
			t.Errorf("fold %s: Go %s, engine %s", id, fs[id].Digest, d)
		}
	}
}

// What the vendored pack holds today: 27 obligations, 24 measured by their
// check (DETERMINISTIC, hence ENFORCED) and 3 declared_not_measured
// (UNAVAILABLE-STATE-REQUIRED, hence UNKNOWN); no outcomes.
func TestEverydayAssertsOnlyWhatExists(t *testing.T) {
	p, err := LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Rules) != 27 {
		t.Fatalf("vendored pack has %d rules, expected the 27 obligations it ships today", len(p.Rules))
	}
	declared := 0
	for _, r := range p.Rules {
		switch {
		case r.Declared:
			declared++
			if r.Check != "" || r.ForwardVerdict != ForwardUnavailableState || r.Implementation() != Unknown {
				t.Errorf("%s: declared, but %q %s / %s", r.ID, r.Check, r.ForwardVerdict, r.Implementation())
			}
		case r.Check == "" || r.ForwardVerdict != ForwardDeterministic || r.Implementation() != Enforced:
			t.Errorf("%s: measured, but %q %s / %s", r.ID, r.Check, r.ForwardVerdict, r.Implementation())
		}
	}
	if declared != 3 {
		t.Errorf("%d declared_not_measured rules, want 3", declared)
	}
}

func TestFixtureDigestMatchesEngineGolden(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(fixtureDir, "GOLDEN.json"))
	if err != nil {
		t.Fatal(err)
	}
	var g golden
	if err := json.Unmarshal(data, &g); err != nil {
		t.Fatal(err)
	}
	raw := fixtureBytes(t)
	if sha256Hex(raw) != g.PackYAMLSHA256 {
		t.Fatalf("fixture pack.yaml changed since the golden was computed")
	}
	p, err := loadFixture(t, raw)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := identityOf(p), (identity{g.PackID, g.EngineDefinitionDigest}); got != want {
		t.Fatalf("Go %+v, engine golden %+v", got, want)
	}
	if len(p.Rules) != 25 {
		t.Fatalf("fixture has %d rules, expected 25", len(p.Rules))
	}
}

// Red on purpose: a rule whose forward verdict is UNAVAILABLE-MODEL-REQUIRED
// can never carry ENFORCED, and neither can any other non-DETERMINISTIC one.
func TestOnlyDeterministicIsEnforced(t *testing.T) {
	p, err := loadFixture(t, fixtureBytes(t))
	if err != nil {
		t.Fatal(err)
	}
	asked := 0
	for _, r := range p.Rules {
		if r.ForwardVerdict == ForwardUnavailableModel {
			asked++
		}
		if r.ForwardVerdict != ForwardDeterministic && r.Implementation() == Enforced {
			t.Errorf("%s: forward verdict %s labelled ENFORCED", r.ID, r.ForwardVerdict)
		}
	}
	if asked != 19 {
		t.Fatalf("fixture has %d UNAVAILABLE-MODEL-REQUIRED rules, expected 19", asked)
	}
	for _, v := range []ForwardVerdict{ForwardUnavailableModel, ForwardUnavailableState, ForwardRefused, "MODEL"} {
		if (Rule{ForwardVerdict: v}).Implementation() == Enforced {
			t.Errorf("forward verdict %q labelled ENFORCED", v)
		}
	}
}

func TestChangingThePackChangesTheDigest(t *testing.T) {
	raw := fixtureBytes(t)
	base, err := loadFixture(t, raw)
	if err != nil {
		t.Fatal(err)
	}
	changed, err := loadFixture(t, bytes.Replace(raw, []byte("before buying from a merchant"), []byte("before paying a merchant"), 1))
	if err != nil {
		t.Fatal(err)
	}
	if changed.DefinitionDigest == base.DefinitionDigest {
		t.Fatal("changing an outcome statement did not change the digest")
	}
}

// UPSTREAM GAP, pinned: at capsule-engine 559288d0f61a an outcome's
// default_disposition is not part of the canonical projection, so changing it
// leaves the digest unchanged (verified against the engine itself). This test
// goes red when the engine starts digesting it; update the port then.
func TestOutcomeDispositionNotDigestCovered(t *testing.T) {
	raw := fixtureBytes(t)
	base, err := loadFixture(t, raw)
	if err != nil {
		t.Fatal(err)
	}
	flipped := bytes.Replace(raw, []byte("default_disposition: DO"), []byte("default_disposition: NEVER"), 1)
	if bytes.Equal(flipped, raw) {
		t.Fatal("fixture has no outcome DO disposition to flip")
	}
	changed, err := loadFixture(t, flipped)
	if err != nil {
		t.Fatal(err)
	}
	if changed.DefinitionDigest != base.DefinitionDigest {
		t.Fatal("outcome default_disposition now changes the digest: the upstream gap closed; update this test and the port comment")
	}
	r, _ := changed.Rule("ordinary-purchases")
	if r.DefaultDisposition != "NEVER" {
		t.Fatalf("flipped disposition not read: %q", r.DefaultDisposition)
	}
}

func TestLoadRefusesWhatThePortCannotCanonicalise(t *testing.T) {
	raw := fixtureBytes(t)
	for name, bad := range map[string][]byte{
		"unknown top-level key": append(bytes.Clone(raw), []byte("profiles: []\n")...),
		"wicket digest drift":   bytes.Replace(raw, []byte("18ab5d489f1e5774"), []byte("28ab5d489f1e5774"), 1),
		"bad forward verdict":   bytes.Replace(raw, []byte("forward_verdict: UNAVAILABLE-MODEL-REQUIRED"), []byte("forward_verdict: MODEL"), 1),
		"bad disposition":       bytes.Replace(raw, []byte("default_disposition: DO"), []byte("default_disposition: GO"), 1),
		"duplicate rule id":     bytes.Replace(raw, []byte("id: shipping-address"), []byte("id: ordinary-purchases"), 1),
	} {
		if _, err := loadFixture(t, bad); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

// The vendored pack is capsule-engine's at the pin (0c93a58, the rc21 pack cut), and the digest the
// engine computed there is the one this build loads: re-vendoring changes
// both on purpose, together.
func TestEverydayIsVendoredFromThePin(t *testing.T) {
	data, err := fs.ReadFile(packs.Everyday038, path.Join(packs.Everyday038Dir, "PROVENANCE.json"))
	if err != nil {
		t.Fatal(err)
	}
	var prov struct {
		SourceCommit           string `json:"source_commit"`
		EngineDefinitionDigest string `json:"engine_definition_digest"`
	}
	if err := json.Unmarshal(data, &prov); err != nil {
		t.Fatal(err)
	}
	const pin, digest = "0c93a58fccb6d39797d55ec7174aae16d24f0923", "ac7697061fd1c50177000648e2458f6236068679fc67ae924870ee8fb1cd8488"
	if prov.SourceCommit != pin || prov.EngineDefinitionDigest != digest {
		t.Fatalf("PROVENANCE names %s @ %s, want %s @ %s", prov.SourceCommit, prov.EngineDefinitionDigest, pin, digest)
	}
	p, err := LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	if p.DefinitionDigest != digest {
		t.Fatalf("the loaded pack digests to %s, the engine computed %s at %s", p.DefinitionDigest, digest, pin)
	}
}
