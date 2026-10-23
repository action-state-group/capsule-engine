package guard

import (
	"bytes"
	"encoding/json"
	"io/fs"
	"path"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/pack"
	"github.com/action-state-group/capsule-engine/go/packs"
)

// The checks refuse to run on any definition digest other than the ported one.
func TestEngineRefusesADifferentDefinition(t *testing.T) {
	_, p := everydayEngine(t)
	for i := range p.Wickets {
		changed := p
		changed.Wickets = append([]pack.Wicket(nil), p.Wickets...)
		changed.Wickets[i].Digest = strings.Repeat("0", 64)
		if _, err := NewEngine(changed); err == nil {
			t.Errorf("engine accepted %s at a different digest", p.Wickets[i].ID)
		}
	}
	changed := p
	changed.Folds = append([]pack.Fold(nil), p.Folds...)
	changed.Folds[0].Digest = strings.Repeat("0", 64)
	if _, err := NewEngine(changed); err == nil {
		t.Error("engine accepted the caps fold at a different digest")
	}
}

func TestEngineRefusesAConfigKeyItDoesNotImplement(t *testing.T) {
	_, p := everydayEngine(t)
	changed := p
	changed.Wickets = append([]pack.Wicket(nil), p.Wickets...)
	for i, w := range changed.Wickets {
		if w.Check != "destination_rail" {
			continue
		}
		var cfg yaml.Node
		if err := yaml.Unmarshal([]byte("action_classes: [money.transfer]\nwatched_rails: [p2p]\nallow_list: [x]\n"), &cfg); err != nil {
			t.Fatal(err)
		}
		changed.Wickets[i].Config = *cfg.Content[0]
	}
	if _, err := NewEngine(changed); err == nil {
		t.Fatal("engine accepted a config key it does not implement")
	}
}

// The vendored fold digests to what capsule-engine computed for it.
func TestFoldDigestMatchesEngine(t *testing.T) {
	var prov struct {
		FoldDigests map[string]string `json:"fold_digests"`
	}
	b, err := fs.ReadFile(packs.Everyday038, path.Join(packs.Everyday038Dir, "PROVENANCE.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &prov); err != nil {
		t.Fatal(err)
	}
	_, p := everydayEngine(t)
	if len(p.Folds) != len(prov.FoldDigests) {
		t.Fatalf("Go has %d folds, the engine %d", len(p.Folds), len(prov.FoldDigests))
	}
	for _, f := range p.Folds {
		if prov.FoldDigests[f.ID] != f.Digest {
			t.Errorf("fold %s: Go %s, engine %s", f.ID, f.Digest, prov.FoldDigests[f.ID])
		}
	}
}

func TestCheckRefusesACitedMandate(t *testing.T) {
	e, _ := everydayEngine(t)
	a := scenarioActions(t, fixtures037)[0]
	a.CitedMandateCapsuleID = ptr(strings.Repeat("a", 64))
	if _, err := e.Check(a, nil); err == nil {
		t.Fatal("decided an action citing a mandate it cannot verify")
	}
}

func TestDecodeActionIsStrict(t *testing.T) {
	for name, in := range map[string]string{
		"unknown field":    `{"verb":"v","operator":"o","developer":"d","timestamp":"2026-08-10T10:00:00Z","colour":"x"}`,
		"no timestamp":     `{"verb":"v","operator":"o","developer":"d"}`,
		"float amount":     `{"verb":"v","operator":"o","developer":"d","timestamp":"2026-08-10T10:00:00Z","amount_minor":1.5}`,
		"missing operator": `{"verb":"v","developer":"d","timestamp":"2026-08-10T10:00:00Z"}`,
	} {
		if _, err := DecodeAction(bytes.NewReader([]byte(in))); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
