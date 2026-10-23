package guard

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"slices"
	"strconv"
	"unicode/utf16"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// priceFloorConfig is price_floor/2.0.1's config: the classes it applies to.
type priceFloorConfig struct {
	ActionClasses []string `yaml:"action_classes"`
}

// boundsKind is price_floor.py BOUNDS_KIND.
const boundsKind = "commercial-bounds/v0"

// checkPriceFloor is capsule-engine guards/checks/price_floor.py at ba76a97
// (price_floor/2.0.1): the action's amount against the user's floor, read
// only from the checker input's commercial_bounds_opening once it opens the
// bounds_commitment the bound task-authority record seals at
// body.bounds_commitment. Below the floor fails; the floor itself passes.
// An opening that does not open the sealed commitment fails, and so does a
// sealed commitment that is not 64 lowercase hex. A record committing to no
// floor puts none in force: out of scope. No opening is an in-scope n/a
// naming commercial_bounds_opening that the report reads as not evaluated:
// a floor is in force and was not read. The floor, the nonce and the
// document are never stated: the evidence holds the commitment the record
// already seals, the action's own amount and whether it is below.
//
// The amount compared is stated_amount_minor when set (capsule-engine #129:
// on a deal record, the body's amount_minor, which on a sale is the price
// while the spend is 0), else the engine's spend (engineAmount).
func checkPriceFloor(a Action, record, opening json.RawMessage, c priceFloorConfig) (out Constraint) {
	const id, method = "price_floor", "committed_floor_threshold_v1"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	// capsule-engine #129 (8522efc): the amount the action states when set
	// (on a deal record the body's amount_minor: a sale's price, where its
	// spend is 0), else the spend the engine reads.
	amount, amountReads := a.StatedAmountMinor, []string{"stated_amount_minor"}
	if amount == nil {
		amount, amountReads = a.engineAmount()
	}
	rd = append(rd, amountReads...)
	if amount == nil {
		return missingField(id, method, "amount_minor")
	}
	rd = append(rd, "task_authority_ref")
	if field, why := bindTaskAuthority(a, record); field != "" {
		c := missingField(id, method, field)
		c.pending = why
		return c
	}
	ref := *a.TaskAuthorityRef
	sealed, committed := sealedCommitment(record)
	if !committed {
		return outOfScope(id, method)
	}
	if !hex64.MatchString(sealed) {
		out = decided(id, method, Fail, canon.Obj(canon.M("task_authority_ref", canon.Str(ref)), canon.M("malformed_field", canon.Str("bounds_commitment"))))
		out.failNote = "the bounds_commitment the task authority seals is not a SHA-256 hex digest"
		return out
	}
	rd = append(rd, "commercial_bounds_opening")
	if isJSONNull(opening) {
		out = missingField(id, method, "commercial_bounds_opening")
		out.pending = "no commercial-bounds opening was supplied; the committed floor could not be read"
		out.unevaluated = true
		return out
	}
	floor, ok := openedFloor(opening, sealed)
	if !ok {
		out = decided(id, method, Fail, canon.Obj(canon.M("task_authority_ref", canon.Str(ref)), canon.M("bounds_commitment", canon.Str(sealed)),
			canon.M("opens_commitment", canon.Bool(false))))
		out.failNote = "the commercial-bounds opening does not open the bounds_commitment the task authority seals"
		return out
	}
	below := *amount < floor
	out = decided(id, method, Pass, canon.Obj(canon.M("task_authority_ref", canon.Str(ref)), canon.M("bounds_commitment", canon.Str(sealed)),
		canon.M("amount_minor", canon.Int(*amount)), canon.M("below_floor", canon.Bool(below))))
	if below {
		out.Result = Fail
		out.failNote = "the amount is below the committed floor"
	}
	return out
}

// sealedCommitment is price_floor.py _commitment_in over the bound record:
// the value at body.bounds_commitment, and whether the record seals one
// there at all. A value that is not a string is returned as "", which no
// commitment matches, so it is refused as malformed, as there.
func sealedCommitment(record json.RawMessage) (string, bool) {
	var top map[string]json.RawMessage
	if json.Unmarshal(record, &top) != nil || top == nil {
		return "", false
	}
	var body map[string]json.RawMessage
	if raw, ok := top["body"]; !ok || json.Unmarshal(raw, &body) != nil || body == nil {
		return "", false
	}
	raw, ok := body["bounds_commitment"]
	if !ok {
		return "", false
	}
	var s string
	if json.Unmarshal(raw, &s) != nil {
		return "", true
	}
	return s, true
}

// openedFloor is price_floor.py _opened_floor: the floor in opening once it
// opens sealed, and false when it does not (a member out of shape, a stated
// commitment other than the sealed one, a nonce and document that do not
// recompute it, or a document that is not commercial-bounds/v0 with a
// non-negative integer floor). The commitment is textCommitment with the
// document's JCS bytes as the text, read as Python reads them (pythonJSON).
func openedFloor(opening json.RawMessage, sealed string) (int64, bool) {
	var o map[string]json.RawMessage
	if json.Unmarshal(opening, &o) != nil || o == nil {
		return 0, false
	}
	var stated, nonce string
	if json.Unmarshal(o["bounds_commitment"], &stated) != nil || stated != sealed {
		return 0, false
	}
	if json.Unmarshal(o["nonce"], &nonce) != nil || !hex64.MatchString(nonce) {
		return 0, false
	}
	var doc map[string]json.RawMessage
	if json.Unmarshal(o["document"], &doc) != nil || doc == nil {
		return 0, false
	}
	v, err := pythonJSON(o["document"])
	if err != nil {
		return 0, false
	}
	text, err := canon.JCS(v)
	if err != nil {
		return 0, false
	}
	got, err := textCommitment(nonce, text)
	if err != nil || got != sealed {
		return 0, false
	}
	var kind string
	if json.Unmarshal(doc["type"], &kind) != nil || kind != boundsKind {
		return 0, false
	}
	floor, ok := jsonInt(doc["min_total_minor"])
	if !ok || floor < 0 {
		return 0, false
	}
	return floor, true
}

// jsonInt is raw as a Python int: a JSON number with no fraction or
// exponent, never true or false.
func jsonInt(raw json.RawMessage) (int64, bool) {
	if !jsonInteger.Match(bytes.TrimSpace(raw)) {
		return 0, false
	}
	i, err := strconv.ParseInt(string(bytes.TrimSpace(raw)), 10, 64)
	return i, err == nil
}

// errNoDigest is a JSON value agent_action_capsule's jcs has no bytes for.
var errNoDigest = errors.New("no JCS digest")

// pythonJSON is raw as json.loads reads it, for digesting: an object's
// repeated key keeps its last value; a number with a fraction or an exponent
// is a float, and an integer past int64 past the safe range, neither of
// which jcs digests; and a lone UTF-16 surrogate escape is a string jcs
// cannot encode (Go's decoder would replace it with U+FFFD and digest that).
func pythonJSON(raw []byte) (canon.Value, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	v, err := pythonValue(dec)
	if err != nil {
		return canon.Value{}, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return canon.Value{}, errNoDigest
	}
	if loneSurrogate(raw) {
		return canon.Value{}, errNoDigest
	}
	return v, nil
}

func pythonValue(dec *json.Decoder) (canon.Value, error) {
	tok, err := dec.Token()
	if err != nil {
		return canon.Value{}, err
	}
	switch t := tok.(type) {
	case json.Delim:
		if t == '[' {
			var items []canon.Value
			for dec.More() {
				v, err := pythonValue(dec)
				if err != nil {
					return canon.Value{}, err
				}
				items = append(items, v)
			}
			_, err := dec.Token()
			return canon.Arr(items), err
		}
		var members []canon.Member
		for dec.More() {
			kt, err := dec.Token()
			if err != nil {
				return canon.Value{}, err
			}
			key, _ := kt.(string)
			v, err := pythonValue(dec)
			if err != nil {
				return canon.Value{}, err
			}
			if i := slices.IndexFunc(members, func(m canon.Member) bool { return m.Key == key }); i >= 0 {
				members[i].Val = v
				continue
			}
			members = append(members, canon.M(key, v))
		}
		_, err := dec.Token()
		return canon.Obj(members...), err
	case string:
		return canon.Str(t), nil
	case bool:
		return canon.Bool(t), nil
	case nil:
		return canon.Null(), nil
	case json.Number:
		if !jsonInteger.MatchString(t.String()) {
			return canon.Value{}, errNoDigest
		}
		i, err := t.Int64()
		if err != nil {
			return canon.Value{}, errNoDigest
		}
		return canon.Int(i), nil
	}
	return canon.Value{}, errNoDigest
}

// loneSurrogate is whether a string in raw holds a \u escape of a UTF-16
// surrogate that is not one half of a pair.
func loneSurrogate(raw []byte) bool {
	inString := false
	for i := 0; i < len(raw); i++ {
		switch {
		case !inString:
			inString = raw[i] == '"'
		case raw[i] == '"':
			inString = false
		case raw[i] == '\\' && i+1 < len(raw) && raw[i+1] != 'u':
			i++
		case raw[i] == '\\':
			r, ok := escapedUnit(raw, i)
			if !ok {
				return true
			}
			i += 5
			if !utf16.IsSurrogate(r) {
				continue
			}
			next, ok := escapedUnit(raw, i+1)
			if r >= 0xDC00 || !ok || next < 0xDC00 || next > 0xDFFF {
				return true
			}
			i += 6
		}
	}
	return false
}

// escapedUnit is the UTF-16 code unit of the \uXXXX escape at raw[i].
func escapedUnit(raw []byte, i int) (rune, bool) {
	if i+6 > len(raw) || raw[i] != '\\' || raw[i+1] != 'u' {
		return 0, false
	}
	n, err := strconv.ParseUint(string(raw[i+2:i+6]), 16, 16)
	return rune(n), err == nil
}
