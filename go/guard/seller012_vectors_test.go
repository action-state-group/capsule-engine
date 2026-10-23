package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// seller012Vectors are capsule-engine's own decisions under asg/seller/0.1.2
// at e0e664d over the S1, S2 and 0.1.1-acceptance (S3) corpora
// (testdata/seller-012-vectors, written by the generators vendored beside
// the earlier vectors, unmodified).
var seller012Vectors = filepath.Join("testdata", "seller-012-vectors")

const seller012Commit = "e0e664de1ede4febae31931579ebe85497b4b120"

// Every vendored file is byte for byte what PROVENANCE.json records, and it
// records every file; the generators and the reused corpora are the ones
// vendored beside the earlier vectors.
func TestSeller012VectorsAreVendoredAsRecorded(t *testing.T) {
	var prov struct {
		EngineCommit string `json:"engine_commit"`
		Generators   map[string]string
		Corpora      struct {
			Files map[string]string `json:"files"`
		} `json:"corpora_reused"`
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, seller012Vectors, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	if prov.EngineCommit != seller012Commit {
		t.Fatalf("vectors at %s", prov.EngineCommit)
	}
	var onDisk, recorded []string
	err := fs.WalkDir(os.DirFS(seller012Vectors), ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || p == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		if got := fileSHA256Hex(readFile(t, seller012Vectors, name)); got != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, got, f.SHA256)
		}
	}
	slices.Sort(onDisk)
	if slices.Sort(recorded); !slices.Equal(onDisk, recorded) || len(recorded) != 7 {
		t.Fatalf("vendored %v, provenance records %v", onDisk, recorded)
	}
	for _, m := range []map[string]string{prov.Generators, prov.Corpora.Files} {
		for name, sum := range m {
			if got := fileSHA256Hex(readFile(t, "testdata", name)); got != sum {
				t.Errorf("%s sha256 %s, provenance records %s", name, got, sum)
			}
		}
	}
	if len(prov.Generators) != 3 || len(prov.Corpora.Files) != 5 {
		t.Errorf("%d generators, %d reused corpora", len(prov.Generators), len(prov.Corpora.Files))
	}
}

// CROSS-IMPLEMENTATION: S1 under asg/seller/0.1.2. 0.1.2 cites no caps
// definition, so caps is n/a, out of scope, on every step, with the engine's
// evidence digest, where under 0.1.0 and 0.1.1 caps/1.0.0 limited the
// corpus's money transfers; every other S1 check is held as before.
func TestSeller012S1ChecksAgreeWithTheEngine(t *testing.T) {
	e, p := seller012Engine(t)
	covered := map[string][]Result{"caps": {NotApplicable}, "offer_expiry": {Pass, Fail, NotApplicable},
		"price_floor": {Pass, Fail, NotApplicable}, "single_commitment": {Pass, Fail, NotApplicable}}
	diffs := runS1Vectors(t, e, p, filepath.Join(seller012Vectors, "s1"), seller012Commit, covered)
	if !slices.Equal(diffs, s1Pre137Differences) {
		t.Errorf("Go and the engine differ at\n%s\npinned\n%s", strings.Join(diffs, "\n"), strings.Join(s1Pre137Differences, "\n"))
	}
}

// CROSS-IMPLEMENTATION: the S2 corpora under asg/seller/0.1.2, each check
// held as under 0.1.0, with its evidence digest; every other check's result,
// and the outcome, are the engine's on every step.
func TestSeller012S2AgreesWithTheEngine(t *testing.T) {
	e, p := seller012Engine(t)
	dir := filepath.Join(seller012Vectors, "s2")
	for name, check := range map[string]string{"rail": "destination_rail", "promise-never": "promise_never",
		"promise-requires-approval": "promise_requires_approval", "required-disclosure": "required_disclosure"} {
		reports := runS2VectorsOn(t, e, p, dir, seller012Commit, name, check)
		noEchoInCorpus(t, name, reports)
	}
}

// CROSS-IMPLEMENTATION: the 0.1.1 acceptance scenario under asg/seller/0.1.2,
// held exactly as under 0.1.1 (release_on_acceptance's result, reason and
// evidence digest; every other check's result; the outcome where no mandate
// is cited).
func TestSeller012ReleaseAgreesWithTheEngine(t *testing.T) {
	e, p := seller012Engine(t)
	runReleaseVectors(t, e, p, filepath.Join(seller012Vectors, "s3", "python-decisions.json"), seller012Commit)
}

func fileSHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
