package guard

import (
	"io/fs"
	"path"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
	"github.com/action-state-group/capsule-engine/go/packs"
)

// composedEngine is an engine over the vendored everyday pack.yaml under a
// test-only pack id, with extra obligations, constraints and folds appended,
// each citing a definition in the vendored catalog by digest. It lets a
// check the shipped pack does not cite yet run through NewEngine and Check
// exactly as a pack citing it would.
func composedEngine(t *testing.T, obligations, constraints, folds string) *Engine {
	t.Helper()
	raw, err := fs.ReadFile(packs.Everyday038, path.Join(packs.Everyday038Dir, "pack.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	y := strings.Replace(string(raw), "pack_id: asg/everyday/0.3.8", "pack_id: test/composed/0.0.0", 1)
	y = strings.Replace(y, "\naction_semantics:", obligations+"\naction_semantics:", 1)
	y = strings.Replace(y, "\nfolds:", constraints+"\nfolds:", 1)
	y = strings.Replace(y, "\nholds_integration:", folds+"\nholds_integration:", 1)
	p, err := pack.Load([]byte(y), packs.Everyday038, packs.Everyday038Dir)
	if err != nil {
		t.Fatalf("compose: %v", err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatalf("engine: %v", err)
	}
	return e
}
