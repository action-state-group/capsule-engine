package canon

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

// FromJSON reads one JSON value into a Value, for digesting a document this
// package did not build (a disclosed agent_input). Numbers must be integers
// in int64: JCS formats other numbers by ECMAScript rules this package does
// not implement, so they are refused rather than digested differently.
func FromJSON(raw []byte) (Value, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	v, err := fromJSON(dec)
	if err != nil {
		return Value{}, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return Value{}, errors.New("canon: trailing data after the JSON value")
	}
	return v, nil
}

func fromJSON(dec *json.Decoder) (Value, error) {
	tok, err := dec.Token()
	if err != nil {
		return Value{}, err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			var members []Member
			seen := map[string]bool{}
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return Value{}, err
				}
				key, ok := kt.(string)
				if !ok {
					return Value{}, fmt.Errorf("canon: object key %v is not a string", kt)
				}
				if seen[key] {
					return Value{}, fmt.Errorf("canon: duplicate object key %q", key)
				}
				seen[key] = true
				val, err := fromJSON(dec)
				if err != nil {
					return Value{}, err
				}
				members = append(members, M(key, val))
			}
			if _, err := dec.Token(); err != nil {
				return Value{}, err
			}
			return Obj(members...), nil
		case '[':
			var items []Value
			for dec.More() {
				val, err := fromJSON(dec)
				if err != nil {
					return Value{}, err
				}
				items = append(items, val)
			}
			if _, err := dec.Token(); err != nil {
				return Value{}, err
			}
			return Arr(items), nil
		}
		return Value{}, fmt.Errorf("canon: unexpected %v", t)
	case string:
		return Str(t), nil
	case bool:
		return Bool(t), nil
	case nil:
		return Null(), nil
	case json.Number:
		i, err := t.Int64()
		if err != nil {
			return Value{}, fmt.Errorf("canon: %s is not an integer in int64; JCS for other numbers is not implemented", t)
		}
		return Int(i), nil
	}
	return Value{}, fmt.Errorf("canon: unexpected token %v", tok)
}
