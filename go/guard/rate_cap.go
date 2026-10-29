package guard

import (
	"fmt"
	"slices"
	"strings"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// rate_cap is capsule-engine guards/checks/rate_cap.py (rate_cap/1.0.0,
// capsule-engine#263): the advertised input and output rates of the peer an
// inference is bought from, in millisatoshis per million tokens (mesh-llm
// Pricing), against two caps. A rate over its cap fails; at its cap it
// passes. An action missing either rate is n/a, in scope, naming the first
// one missing, and fails closed: a price that cannot be read is refused. The
// wicket ships both caps at 2^53-1, and a profile sets them (WithProfile).

const rateCapMethod = "rate_threshold_v0"

// rateCapConfig is rate_cap's config.
type rateCapConfig struct {
	ActionClasses           []string `yaml:"action_classes"`
	MaxInputMsatPerMillion  int64    `yaml:"max_input_msat_per_million"`
	MaxOutputMsatPerMillion int64    `yaml:"max_output_msat_per_million"`
}

// rateCapKeys are the rate_cap keys a profile may set (policy/profile.py
// RATE_CAP_KEYS), each beside the action field it caps (rate_cap.py
// RATE_FIELDS), in the order the check reads them.
var rateCapKeys = []struct{ field, cap string }{
	{"input_msat_per_million", "max_input_msat_per_million"},
	{"output_msat_per_million", "max_output_msat_per_million"},
}

func (c rateCapConfig) capFor(key string) int64 {
	if key == "max_input_msat_per_million" {
		return c.MaxInputMsatPerMillion
	}
	return c.MaxOutputMsatPerMillion
}

func (a Action) rateFor(field string) *int64 {
	if field == "input_msat_per_million" {
		return a.InputMsatPerMillion
	}
	return a.OutputMsatPerMillion
}

// checkRateCap is rate_cap.py check_rate_cap.
func checkRateCap(a Action, cfg rateCapConfig) (out Constraint) {
	const id = "rate_cap"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(cfg.ActionClasses, a.class()) {
		return outOfScope(id, rateCapMethod)
	}
	rates := map[string]int64{}
	for _, k := range rateCapKeys {
		rd = append(rd, k.field)
		r := a.rateFor(k.field)
		if r == nil {
			out = missingField(id, rateCapMethod, k.field)
			out.failsClosed = true
			return out
		}
		rates[k.field] = *r
	}
	var over []canon.Value
	var notes []string
	for _, k := range rateCapKeys {
		limit, observed := cfg.capFor(k.cap), rates[k.field]
		if observed > limit {
			over = append(over, canon.Obj(canon.M("cap", canon.Str(k.cap)),
				canon.M("cap_msat_per_million", canon.Int(limit)), canon.M("observed_msat_per_million", canon.Int(observed))))
			notes = append(notes, fmt.Sprintf("%s %d exceeded by %d", k.cap, limit, observed))
		}
	}
	if over == nil {
		over = []canon.Value{}
	}
	evidence := canon.Obj(
		canon.M("input_msat_per_million", canon.Int(rates["input_msat_per_million"])),
		canon.M("output_msat_per_million", canon.Int(rates["output_msat_per_million"])),
		canon.M("max_input_msat_per_million", canon.Int(cfg.MaxInputMsatPerMillion)),
		canon.M("max_output_msat_per_million", canon.Int(cfg.MaxOutputMsatPerMillion)),
		canon.M("over", canon.Arr(over)))
	r := Pass
	if len(notes) > 0 {
		r = Fail
	}
	out = decided(id, rateCapMethod, r, evidence)
	if r == Fail {
		out.failNote = strings.Join(notes, "; ")
	}
	return out
}
