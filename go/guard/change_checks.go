package guard

import (
	"fmt"
	"slices"
	"strconv"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// The checks that read a producer's change scalars (capsule-engine 365eb92
// guards/checks/channel_change.py and field_change_count.py), each off the
// one action, reading no history.

type channelConfig struct {
	ActionClasses  []string `yaml:"action_classes"`
	SeededChannels []string `yaml:"seeded_channels"`
}

// checkChannelChange is check_channel_change: out of scope for a class the
// wicket does not list (the raw class); a missing channel, then a missing
// first_contact_channel, is a population-a n/a; otherwise the two kinds are
// compared for equality, a namespaced (unseeded) one the same way, and the
// evidence records whether each is seeded.
func checkChannelChange(a Action, c channelConfig) (out Constraint) {
	const id, method = "channel_change", "same_record_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "channel")
	if a.Channel == nil {
		return missingField(id, method, "channel")
	}
	rd = append(rd, "first_contact_channel")
	if a.FirstContactChannel == nil {
		return missingField(id, method, "first_contact_channel")
	}
	evidence := canon.Obj(canon.M("channel", canon.Str(*a.Channel)), canon.M("first_contact_channel", canon.Str(*a.FirstContactChannel)),
		canon.M("channel_seeded", canon.Bool(slices.Contains(c.SeededChannels, *a.Channel))),
		canon.M("first_contact_channel_seeded", canon.Bool(slices.Contains(c.SeededChannels, *a.FirstContactChannel))))
	r := Pass
	if *a.Channel != *a.FirstContactChannel {
		r = Fail
	}
	out = decided(id, method, r, evidence)
	out.read = &Read{Field: "channel", Value: *a.FirstContactChannel + " -> " + *a.Channel}
	return out
}

// fieldCountConfig is the config of the two pinned-list counts
// (guards/checks/field_change_count.py): the classes, the pinned list and
// the most fields that may change.
type fieldCountConfig struct {
	ActionClasses []string `yaml:"action_classes"`
	CountedFields []string `yaml:"counted_fields"`
	MaxChanged    int64    `yaml:"max_changed"`
}

// fieldCount is a pinned-list count with its list's basis computed once.
type fieldCount struct {
	fieldCountConfig
	basis string
}

// newFieldCount decodes the config and computes fields_basis: SHA-256 over
// the JCS bytes of the list, in the order the wicket config pins it.
func newFieldCount(w pack.Wicket) (fieldCount, error) {
	cfg, err := decodeConfig[fieldCountConfig](w)
	if err != nil {
		return fieldCount{}, err
	}
	basis, err := canon.Digest(canon.Strs(cfg.CountedFields))
	if err != nil {
		return fieldCount{}, fmt.Errorf("%s counted_fields: %w", w.ID, err)
	}
	return fieldCount{fieldCountConfig: cfg, basis: basis}, nil
}

// checkFieldCount is field_change_count.py _check: out of scope for a class
// the wicket does not list (the raw class); no count is a population-a n/a
// naming the count field; a basis that is absent or not the pinned list's
// is a population-a n/a naming the basis field (the count cannot be read as
// one over the pinned list); otherwise it fails when the count is above
// max_changed. It never reads the fields or their values.
func checkFieldCount(id, countField, basisField string, count *int64, basis *string, fc fieldCount, a Action) (out Constraint) {
	const method = "pinned_field_count_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(fc.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, countField)
	if count == nil {
		return missingField(id, method, countField)
	}
	rd = append(rd, basisField)
	if basis == nil || *basis != fc.basis {
		return missingField(id, method, basisField)
	}
	evidence := canon.Obj(canon.M("changed", canon.Int(*count)), canon.M("fields_basis", canon.Str(fc.basis)),
		canon.M("max_changed", canon.Int(fc.MaxChanged)))
	r := Pass
	if *count > fc.MaxChanged {
		r = Fail
	}
	out = decided(id, method, r, evidence)
	out.read = &Read{Field: countField, Value: strconv.FormatInt(*count, 10)}
	return out
}
