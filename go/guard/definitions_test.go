package guard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// engineDefinitions is testdata/engine-definitions.json, which
// scripts/generate_go_packs.py writes from the engine's catalog.
type engineDefinitions struct {
	Wickets map[string]string `json:"wickets"`
	Folds   map[string]string `json:"folds"`
	Retired map[string]string `json:"retired"`
	Current map[string]string `json:"current"`
}

func readEngineDefinitions(t *testing.T) engineDefinitions {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "engine-definitions.json"))
	if err != nil {
		t.Fatal(err)
	}
	var d engineDefinitions
	if err := json.Unmarshal(raw, &d); err != nil {
		t.Fatal(err)
	}
	return d
}

// Every definition this port implements is one the engine's catalog holds at
// that digest, and none is retired: a pack citing a retired pair is refused
// here as the engine refuses it, because NewEngine refuses every pair not in
// portedDefinitions.
func TestPortedDefinitionsAreTheCatalogs(t *testing.T) {
	d := readEngineDefinitions(t)
	for id, digest := range portedDefinitions {
		if d.Wickets[id] != digest && d.Folds[id] != digest {
			t.Errorf("%s at %s is not in the engine's catalog", id, digest)
		}
		if d.Retired[id] == digest {
			t.Errorf("%s at %s is retired", id, digest)
		}
	}
}

// Every embedded pack builds an engine, and the current version of each
// served pack has no rule this port has not run yet.
func TestEveryEmbeddedPackBuildsAnEngine(t *testing.T) {
	d := readEngineDefinitions(t)
	current := map[string]bool{}
	for _, dir := range d.Current {
		current[dir] = true
	}
	found := map[string]bool{}
	for _, v := range pack.Embedded() {
		p, err := v.Load()
		if err != nil {
			t.Fatalf("%s: %v", v.Dir, err)
		}
		if _, err := NewEngine(p); err != nil {
			t.Errorf("%s: %v", v.Dir, err)
		}
		if current[v.Dir] {
			found[v.Dir] = true
			if rules := NotYetRunRules(p); len(rules) > 0 {
				t.Errorf("%s, the current version, has rules not yet run: %v", v.Dir, rules)
			}
		}
	}
	for dir := range current {
		if !found[dir] {
			t.Errorf("the current version %s is not embedded", dir)
		}
	}
}
