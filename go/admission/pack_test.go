package admission

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

func ladderSource(t *testing.T) []byte {
	t.Helper()
	src, err := os.ReadFile(filepath.Join(vectorDir, "packs", "ladder.json"))
	if err != nil {
		t.Fatal(err)
	}
	return src
}

func TestLoadPinned(t *testing.T) {
	src := ladderSource(t)
	p, err := Load(src)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPinned(src, p.ID, p.Digest); err != nil {
		t.Errorf("the pinned bytes: %v", err)
	}
	other := []byte(strings.Replace(string(src), `"deadline_s":86400`, `"deadline_s":86401`, 1))
	if _, err := LoadPinned(other, p.ID, p.Digest); err == nil {
		t.Error("changed bytes under the same pin load")
	}
	if _, err := LoadPinned(src, "asg/other/0.1.0", p.Digest); err == nil {
		t.Error("another pack id loads under this pin")
	}
}

// refusalVectors is conformance-vectors/admission/v0/refusals.json: the
// admission design rules (capsule-engine#265) as the loader holds them, and
// the exact-JCS rule. The Rust loader decides the same file.
type refusalVectors struct {
	Cases []struct {
		Case string `json:"case"`
		Base string `json:"base"`
		Ops  []struct {
			Set    []any `json:"set"`
			Delete []any `json:"delete"`
			Value  any   `json:"value"`
		} `json:"ops"`
		Want string `json:"want"`
	} `json:"cases"`
	Raw []struct {
		Case  string `json:"case"`
		Bytes string `json:"bytes"`
		Want  string `json:"want"`
	} `json:"raw"`
}

// applyOp sets or deletes the member at path (object keys and array
// indexes) in doc.
func applyOp(doc any, path []any, value any, del bool) any {
	if len(path) == 0 {
		return value
	}
	switch node := doc.(type) {
	case map[string]any:
		k := path[0].(string)
		if len(path) == 1 && del {
			delete(node, k)
			return node
		}
		node[k] = applyOp(node[k], path[1:], value, del)
		return node
	case []any:
		n, err := path[0].(json.Number).Int64()
		if err != nil {
			panic("refusals.json: an array index that is not an integer")
		}
		i := int(n)
		node[i] = applyOp(node[i], path[1:], value, del)
		return node
	}
	panic("refusals.json: a path through a scalar")
}

func TestRefusalVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(vectorDir, "refusals.json"))
	if err != nil {
		t.Fatal(err)
	}
	var v refusalVectors
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		t.Fatal(err)
	}
	if len(v.Cases) == 0 || len(v.Raw) == 0 {
		t.Fatal("no refusal vectors")
	}
	refused := func(name string, src []byte, want string) {
		if _, err := Load(src); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%s: got %v; want an error containing %q", name, err, want)
		}
	}
	for _, c := range v.Cases {
		base, err := os.ReadFile(filepath.Join(vectorDir, filepath.FromSlash(c.Base)))
		if err != nil {
			t.Fatal(err)
		}
		bd := json.NewDecoder(strings.NewReader(string(base)))
		bd.UseNumber()
		var doc any
		if err := bd.Decode(&doc); err != nil {
			t.Fatal(err)
		}
		for _, op := range c.Ops {
			if op.Delete != nil {
				doc = applyOp(doc, op.Delete, nil, true)
			} else {
				doc = applyOp(doc, op.Set, op.Value, false)
			}
		}
		js, err := json.Marshal(doc)
		if err != nil {
			t.Fatal(err)
		}
		cv, err := canon.FromJSON(js)
		if err != nil {
			t.Fatal(err)
		}
		jcs, err := canon.JCS(cv)
		if err != nil {
			t.Fatal(err)
		}
		refused(c.Case, []byte(jcs), c.Want)
	}
	for _, c := range v.Raw {
		refused(c.Case, []byte(c.Bytes), c.Want)
	}
}
