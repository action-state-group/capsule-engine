package pack

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// sourceEdited is the vendored asg/everyday/0.3.8 pack-source.yaml with one
// edit, loaded as a source against its own definitions.
func sourceEdited(t *testing.T, old, new string) error {
	t.Helper()
	y := string(vendoredPackYAML(t))
	if !strings.Contains(y, old) {
		t.Fatalf("the vendored pack-source.yaml has no %q", old)
	}
	_, err := LoadSource([]byte(strings.Replace(y, old, new, 1)), packs.Everyday038, packs.Everyday038Dir)
	return err
}

// asLegacy is the vendored pack-source.yaml with its source-only fields
// dropped: the pack.yaml it was moved from.
func asLegacy(t *testing.T) string {
	t.Helper()
	var out []string
	prev := ""
	for _, line := range strings.SplitAfter(string(vendoredPackYAML(t)), "\n") {
		// A constraint's check is the line right after its wicket_ref.
		citedCheck := strings.HasPrefix(strings.TrimSpace(line), "check:") && strings.HasPrefix(strings.TrimSpace(prev), "- wicket_ref:")
		prev = line
		if strings.HasPrefix(line, "pack_version:") || line == "scope:\n" || line == "  kind: live\n" || citedCheck {
			continue
		}
		out = append(out, line)
	}
	return strings.Join(out, "")
}

// The source-only fields are not in the digest: the pack.yaml a source was
// moved from loads at the same digest through the deprecated read path.
func TestTheSourceAndItsPackYAMLHaveOneDigest(t *testing.T) {
	src, err := LoadSource(vendoredPackYAML(t), packs.Everyday038, packs.Everyday038Dir)
	if err != nil {
		t.Fatal(err)
	}
	legacy, err := Load([]byte(asLegacy(t)), packs.Everyday038, packs.Everyday038Dir)
	if err != nil {
		t.Fatal(err)
	}
	if src.DefinitionDigest != legacy.DefinitionDigest || src.DefinitionDigest != readEverydayProvenance(t).EngineDefinitionDigest {
		t.Fatalf("source %s, pack.yaml %s", src.DefinitionDigest, legacy.DefinitionDigest)
	}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, LegacyFile), []byte(asLegacy(t)), 0o600); err != nil {
		t.Fatal(err)
	}
	p, err := LoadPath(dir, builtInCatalog)
	if err != nil || p.DefinitionDigest != src.DefinitionDigest {
		t.Fatalf("a pack.yaml directory: %s %v", p.DefinitionDigest, err)
	}
}

func TestASourceIsRefusedWhenItsSourceFieldsAreWrong(t *testing.T) {
	for _, c := range []struct{ name, old, new, want string }{
		{"after_the_fact scope", "kind: live", "kind: after_the_fact", "scope: {kind: live}"},
		{"no scope", "scope:\n  kind: live\n", "", "scope: {kind: live}"},
		{"pack_version", "pack_version: 0.3.8", "pack_version: 0.3.9", "pack_version"},
		{"no pack_version", "pack_version: 0.3.8\n", "", "pack_version"},
		{"wrong check", "wicket_ref: dedupe/1.0.0\n    check: dedupe", "wicket_ref: dedupe/1.0.0\n    check: caps", "whose check is \"dedupe\""},
		{"requirement_tier", "default_disposition: ASK", "default_disposition: ASK\n    requirement_tier: optional", "requirement_tier"},
	} {
		err := sourceEdited(t, c.old, c.new)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: %v", c.name, err)
		}
	}
	if err := sourceEdited(t, "default_disposition: ASK", "default_disposition: ASK\n    requirement_tier: informational"); err != nil {
		t.Errorf("a known requirement_tier: %v", err)
	}
}

// A pack.yaml carries none of the source-only fields.
func TestAPackYAMLCarryingSourceFieldsIsRefused(t *testing.T) {
	if _, err := Load(vendoredPackYAML(t), packs.Everyday038, packs.Everyday038Dir); err == nil ||
		!strings.Contains(err.Error(), "belong in a pack-source.yaml") {
		t.Fatalf("a pack.yaml with source fields: %v", err)
	}
}

// A directory holding both files has no one definition.
func TestADirectoryWithBothFilesIsRefused(t *testing.T) {
	dir := writePack(t, vendoredPackYAML(t))
	if err := os.WriteFile(filepath.Join(dir, LegacyFile), []byte(asLegacy(t)), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPath(dir, builtInCatalog); err == nil || !strings.Contains(err.Error(), "keep only pack-source.yaml") {
		t.Fatalf("both files: %v", err)
	}
}
