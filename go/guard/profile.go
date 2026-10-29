package guard

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strings"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// ProfileFormat is capsule-engine policy/profile.py PROFILE_FORMAT.
const ProfileFormat = "policy-profile/v0"

// ProfilePack is the values one policy profile sets for one pack
// (policy/profile.py PackParameters). A nil field is one the profile does
// not set.
type ProfilePack struct {
	// Caps are the caps limits it sets, by key (caps_minor,
	// per_action_minor) and action class as written. WithProfile refuses
	// them: this port reads caps limits through OperatorLimits.
	Caps map[string]map[string]int64
	// RateCap are the rate_cap caps it sets (rate_cap.py RATE_FIELDS keys).
	RateCap map[string]int64
	// List is the counterparty list it sets; Disposition is empty when it
	// keeps the wicket's.
	List *listConfig
}

// Profile is a policy-profile/v0 read by DecodeProfile: its digest, the one
// a manifest pins, and its values by pack name (publisher/name, no version).
type Profile struct {
	Digest string
	Packs  map[string]ProfilePack
}

// profilePackName is profile.py PACK_NAME_RE: publisher/name, no version.
var profilePackName = regexp.MustCompile(`^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9-]*$`)

// maxSafeInt is profile.py _MAX_SAFE_INT: the largest integer every JSON
// reader holds exactly.
const maxSafeInt = 1<<53 - 1

// DecodeProfile reads a policy-profile/v0 as profile.py parse_profile does,
// refusing what it refuses: an unknown key, a limit or cap that is not an
// integer from 0 to 2^53-1, a list with no mode, an entry whose kind and
// fp_alg do not go together, a counterparty listed twice, a pack named twice.
// It also refuses a check other than caps, rate_cap and counterparty_list,
// which parse_profile reads and resolve.py refuses. Its digest is the JCS
// SHA-256 of the canonical form (entries in sortedEntries order). Whether
// each value replaces a default the engine's pack declares is checked by
// WithProfile.
func DecodeProfile(raw []byte) (Profile, error) {
	var doc struct {
		Format string            `json:"format"`
		Packs  []json.RawMessage `json:"packs"`
	}
	if err := strictJSON(raw, &doc); err != nil {
		return Profile{}, fmt.Errorf("profile: %w", err)
	}
	if doc.Format != ProfileFormat {
		return Profile{}, fmt.Errorf("profile: format must be %q, got %q", ProfileFormat, doc.Format)
	}
	if doc.Packs == nil {
		return Profile{}, errors.New("profile: packs must be a list")
	}
	out := Profile{Packs: map[string]ProfilePack{}}
	var packsCanon []canon.Value
	for _, rawPack := range doc.Packs {
		var entry struct {
			Pack       string                                `json:"pack"`
			Parameters map[string]map[string]json.RawMessage `json:"parameters"`
		}
		if err := strictJSON(rawPack, &entry); err != nil {
			return Profile{}, fmt.Errorf("profile: packs entry: %w", err)
		}
		if !profilePackName.MatchString(entry.Pack) {
			return Profile{}, fmt.Errorf("profile: packs[].pack must be '<publisher>/<name>' with no version, got %q", entry.Pack)
		}
		if _, dup := out.Packs[entry.Pack]; dup {
			return Profile{}, fmt.Errorf("profile: pack %q appears more than once", entry.Pack)
		}
		values, params, err := decodeProfilePack(entry.Pack, entry.Parameters)
		if err != nil {
			return Profile{}, err
		}
		out.Packs[entry.Pack] = values
		packsCanon = append(packsCanon, canon.Obj(canon.M("pack", canon.Str(entry.Pack)), canon.M("parameters", params)))
	}
	if packsCanon == nil {
		packsCanon = []canon.Value{}
	}
	digest, err := canon.Digest(canon.Obj(canon.M("format", canon.Str(ProfileFormat)), canon.M("packs", canon.Arr(packsCanon))))
	if err != nil {
		return Profile{}, fmt.Errorf("profile: %w", err)
	}
	out.Digest = digest
	return out, nil
}

// decodeProfilePack is one pack's parameters, and their canonical form.
func decodeProfilePack(name string, raw map[string]map[string]json.RawMessage) (ProfilePack, canon.Value, error) {
	var out ProfilePack
	var checks []canon.Member
	for _, check := range slices.Sorted(maps.Keys(raw)) {
		keys := raw[check]
		ctx := fmt.Sprintf("profile: packs[%q].parameters[%q]", name, check)
		switch check {
		case "caps":
			var members []canon.Member
			for _, key := range slices.Sorted(maps.Keys(keys)) {
				values, v, err := decodeClassValues(keys[key], ctx+"["+key+"]")
				if err != nil {
					return out, canon.Value{}, err
				}
				if out.Caps == nil {
					out.Caps = map[string]map[string]int64{}
				}
				out.Caps[key] = values
				members = append(members, canon.M(key, v))
			}
			checks = append(checks, canon.M(check, canon.Obj(members...)))
		case "rate_cap":
			caps, v, err := decodeRateCaps(keys, ctx)
			if err != nil {
				return out, canon.Value{}, err
			}
			out.RateCap = caps
			checks = append(checks, canon.M(check, v))
		case "counterparty_list":
			list, v, err := decodeProfileList(keys, ctx)
			if err != nil {
				return out, canon.Value{}, err
			}
			out.List = &list
			checks = append(checks, canon.M(check, v))
		default:
			return out, canon.Value{}, fmt.Errorf("%s: no wicket lets a user set %q", ctx, check)
		}
	}
	return out, canon.Obj(checks...), nil
}

// safeInt reads raw as an integer from 0 to 2^53-1, as profile.py does.
func safeInt(raw json.RawMessage) (int64, bool) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var n json.Number
	if dec.Decode(&n) != nil {
		return 0, false
	}
	v, err := n.Int64()
	return v, err == nil && v >= 0 && v <= maxSafeInt
}

func decodeClassValues(raw json.RawMessage, ctx string) (map[string]int64, canon.Value, error) {
	var values map[string]json.RawMessage
	if err := json.Unmarshal(raw, &values); err != nil || values == nil {
		return nil, canon.Value{}, fmt.Errorf("%s must be a mapping of action class to an integer", ctx)
	}
	out := map[string]int64{}
	var members []canon.Member
	for _, class := range slices.Sorted(maps.Keys(values)) {
		n, ok := safeInt(values[class])
		if class == "" || !ok {
			return nil, canon.Value{}, fmt.Errorf("%s[%q] must be an integer from 0 to %d, got %s", ctx, class, int64(maxSafeInt), values[class])
		}
		out[class] = n
		members = append(members, canon.M(class, canon.Int(n)))
	}
	return out, canon.Obj(members...), nil
}

// decodeRateCaps is profile.py _parse_rate_caps: at least one of the two
// caps, each an integer from 0 to 2^53-1.
func decodeRateCaps(keys map[string]json.RawMessage, ctx string) (map[string]int64, canon.Value, error) {
	if len(keys) == 0 {
		return nil, canon.Value{}, fmt.Errorf("%s may set only max_input_msat_per_million and max_output_msat_per_million, and at least one", ctx)
	}
	out := map[string]int64{}
	var members []canon.Member
	for _, key := range slices.Sorted(maps.Keys(keys)) {
		if !slices.ContainsFunc(rateCapKeys, func(k struct{ field, cap string }) bool { return k.cap == key }) {
			return nil, canon.Value{}, fmt.Errorf("%s may set only max_input_msat_per_million and max_output_msat_per_million, got %q", ctx, key)
		}
		n, ok := safeInt(keys[key])
		if !ok {
			return nil, canon.Value{}, fmt.Errorf("%s[%q] must be an integer from 0 to %d, got %s", ctx, key, int64(maxSafeInt), keys[key])
		}
		out[key] = n
		members = append(members, canon.M(key, canon.Int(n)))
	}
	return out, canon.Obj(members...), nil
}

// decodeProfileList is profile.py _parse_list_parameters: mode and entries,
// and disposition when given (the wicket's otherwise).
func decodeProfileList(keys map[string]json.RawMessage, ctx string) (listConfig, canon.Value, error) {
	var out listConfig
	for k := range keys {
		if k != "mode" && k != "entries" && k != "disposition" {
			return out, canon.Value{}, fmt.Errorf("%s must set 'mode' and 'entries' and may set 'disposition', got %q", ctx, k)
		}
	}
	if keys["mode"] == nil || keys["entries"] == nil {
		return out, canon.Value{}, fmt.Errorf("%s must set 'mode' and 'entries'", ctx)
	}
	if err := json.Unmarshal(keys["mode"], &out.Mode); err != nil || !slices.Contains(listModes, out.Mode) {
		return out, canon.Value{}, fmt.Errorf("%s['mode'] must be one of %v, got %s", ctx, listModes, keys["mode"])
	}
	var rawEntries []json.RawMessage
	if err := json.Unmarshal(keys["entries"], &rawEntries); err != nil || rawEntries == nil {
		return out, canon.Value{}, fmt.Errorf("%s['entries'] must be a list", ctx)
	}
	for _, re := range rawEntries {
		var e struct {
			Kind  string  `json:"kind"`
			Value string  `json:"value"`
			FPAlg *string `json:"fp_alg"`
		}
		if err := strictJSON(re, &e); err != nil {
			return out, canon.Value{}, fmt.Errorf("%s['entries']: %w", ctx, err)
		}
		entry := ListEntry{Kind: e.Kind, Value: e.Value}
		if e.FPAlg != nil {
			if *e.FPAlg == "" {
				return out, canon.Value{}, fmt.Errorf("%s['entries'] entry has an empty fp_alg", ctx)
			}
			entry.FPAlg = *e.FPAlg
		}
		if err := entry.check(); err != nil {
			return out, canon.Value{}, fmt.Errorf("%s['entries']: %w", ctx, err)
		}
		if slices.Contains(out.Entries, entry) {
			return out, canon.Value{}, fmt.Errorf("%s['entries'] lists a counterparty more than once", ctx)
		}
		out.Entries = append(out.Entries, entry)
	}
	sorted, err := sortedEntries(out.Entries)
	if err != nil {
		return out, canon.Value{}, err
	}
	out.Entries = sorted
	members := []canon.Member{canon.M("mode", canon.Str(out.Mode)), canon.M("entries", entriesValue(sorted))}
	if keys["disposition"] != nil {
		if err := json.Unmarshal(keys["disposition"], &out.Disposition); err != nil || !slices.Contains(listDispositions, out.Disposition) {
			return out, canon.Value{}, fmt.Errorf("%s['disposition'] must be one of %v, got %s", ctx, listDispositions, keys["disposition"])
		}
		members = append(members, canon.M("disposition", canon.Str(out.Disposition)))
	}
	return out, canon.Obj(members...), nil
}

// strictJSON decodes raw into v, refusing an unknown key.
func strictJSON(raw []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

// packName is profile.py pack_name: asg/everyday/0.1.0 -> asg/everyday.
func packName(packID string) string {
	if i := strings.LastIndex(packID, "/"); i >= 0 {
		return packID[:i]
	}
	return packID
}

// WithProfile makes e decide with p's values laid over its pack's wickets,
// as an engine built from a pack installed with p does
// (policy/resolve.py configured_wickets): p's rate_cap caps and counterparty
// list replace the wicket's. It refuses, as resolve.py _check_profile does,
// values for a pack other than e's and for a check e's pack does not cite. It
// also refuses caps limits: this port reads them only through OperatorLimits
// (WithOperatorLimits), whose caller applies an activation's cooling-off.
func (e *Engine) WithProfile(p Profile) (*Engine, error) {
	name := packName(e.pack.ID)
	for n := range p.Packs {
		if n != name {
			return nil, fmt.Errorf("profile sets values for pack %q, which this engine does not run", n)
		}
	}
	values := p.Packs[name]
	if values.Caps != nil {
		return nil, errors.New("profile sets caps limits, which this port does not apply from a profile: " +
			"give them through WithOperatorLimits")
	}
	if values.RateCap != nil {
		w, ok := e.pack.Wicket("rate_cap")
		if !ok {
			return nil, errors.New("profile sets rate_cap, which no wicket of this pack runs")
		}
		var declared map[string]any
		if err := w.Config.Decode(&declared); err != nil {
			return nil, fmt.Errorf("%s config: %w", w.ID, err)
		}
		cfg := e.rateCap
		for key, v := range values.RateCap {
			if _, ok := declared[key]; !ok {
				return nil, fmt.Errorf("profile sets rate_cap.%s, which wicket %s declares no default for", key, w.ID)
			}
			if key == "max_input_msat_per_million" {
				cfg.MaxInputMsatPerMillion = v
			} else {
				cfg.MaxOutputMsatPerMillion = v
			}
		}
		e.rateCap = cfg
	}
	if values.List != nil {
		if _, ok := e.pack.Wicket("counterparty_list"); !ok {
			return nil, errors.New("profile sets counterparty_list, which no wicket of this pack runs")
		}
		list := e.list
		list.Mode, list.Entries = values.List.Mode, values.List.Entries
		if values.List.Disposition != "" {
			list.Disposition = values.List.Disposition
		}
		if err := list.check(); err != nil {
			return nil, err
		}
		e.list = list
	}
	return e, nil
}
