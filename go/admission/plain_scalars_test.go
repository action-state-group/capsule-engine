package admission

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

// TestPlainScalarRule decides the shared probes in
// conformance-vectors/admission/v0/plain-scalars.json: the documents every
// reader of an admission pack (this loader, the Rust one and the lint) must
// accept or refuse alike.
func TestPlainScalarRule(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(vectorDir, "plain-scalars.json"))
	if err != nil {
		t.Fatal(err)
	}
	var probes struct {
		Accepted []string `json:"accepted"`
		Refused  []string `json:"refused"`
	}
	if err := json.Unmarshal(raw, &probes); err != nil {
		t.Fatal(err)
	}
	check := func(doc string) error {
		var n yaml.Node
		if err := yaml.Unmarshal([]byte(doc), &n); err != nil {
			return err
		}
		if v := yaml11Boolean(&n); v != "" {
			return errors.New("a YAML 1.1 boolean: " + v)
		}
		return plainScalars(&n)
	}
	for _, doc := range probes.Accepted {
		if err := check(doc); err != nil {
			t.Errorf("%q: refused (%v); want it accepted", doc, err)
		}
	}
	for _, doc := range probes.Refused {
		if check(doc) == nil {
			t.Errorf("%q: accepted; want it refused", doc)
		}
	}
}
