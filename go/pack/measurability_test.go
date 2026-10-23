package pack

import (
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/packs"
)

func shippedYAML(t *testing.T) string {
	t.Helper()
	b, err := packs.Everyday037.ReadFile(packs.Everyday037Dir + "/pack.yaml")
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// packs/loader.py at 365eb92: a declared_not_measured obligation cites no
// check and names its instrument; a measured one names no instrument. Each
// edit of the shipped pack's own r14 declaration, or of its measured caps
// rule, is refused.
func TestMeasurabilityRefusals(t *testing.T) {
	y := shippedYAML(t)
	declared := "    measurability: declared_not_measured\n    evidence_instrument: {kind: structured_field, field: commitment_classification}\n"
	if !strings.Contains(y, declared) {
		t.Fatal("the shipped pack lacks the r14 declaration")
	}
	for name, edited := range map[string]string{
		"declared and checked":    strings.Replace(y, declared, declared+"    check: caps\n", 1),
		"declared, no instrument": strings.Replace(y, declared, "    measurability: declared_not_measured\n", 1),
		"bad measurability":       strings.Replace(y, declared, "    measurability: sometimes\n    evidence_instrument: {kind: structured_field, field: commitment_classification}\n", 1),
		"instrument, no field":    strings.Replace(y, declared, "    measurability: declared_not_measured\n    evidence_instrument: {kind: structured_field}\n", 1),
		"measured with an instrument": strings.Replace(y, "    check: caps\n    default_disposition: ASK\n",
			"    check: caps\n    evidence_instrument: {kind: structured_field, field: x}\n    default_disposition: ASK\n", 1),
	} {
		if edited == y {
			t.Fatalf("%s: edit did not apply", name)
		}
		if _, err := Load([]byte(edited), packs.Everyday037, packs.Everyday037Dir); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
