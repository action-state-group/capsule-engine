// Package canon is RFC 8785 JCS and JSON-DIGEST as agent_action_capsule
// (0.6.0, agent_action_capsule/canonical.py) implements them: the encoding
// capsule-engine digests pack, wicket, fold and evidence objects with.
package canon

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"unicode/utf16"

	"gopkg.in/yaml.v3"
)

// maxSafeInteger is the ±(2^53-1) bound agent_action_capsule's JCS enforces.
const maxSafeInteger = 1<<53 - 1

type kind int

const (
	kString kind = iota
	kInt
	kBool
	kNull
	kArray
	kObject
)

// Value is the JSON value tree the canonical pack projection is built from:
// only the kinds the engine's JCS accepts (no floats).
type Value struct {
	kind    kind
	str     string
	num     int64
	b       bool
	items   []Value
	members []Member
}

// Member is one object member.
type Member struct {
	Key string
	Val Value
}

// Str is a JSON string.
func Str(s string) Value { return Value{kind: kString, str: s} }

// Int is a JSON integer (the encoder refuses one outside the ±(2^53-1) range).
func Int(i int64) Value { return Value{kind: kInt, num: i} }

// Bool is a JSON boolean.
func Bool(b bool) Value { return Value{kind: kBool, b: b} }

// Null is JSON null.
func Null() Value { return Value{kind: kNull} }

// Strs is a JSON array of strings.
func Strs(ss []string) Value {
	items := make([]Value, 0, len(ss))
	for _, s := range ss {
		items = append(items, Str(s))
	}
	return Value{kind: kArray, items: items}
}

// Arr is a JSON array.
func Arr(items []Value) Value { return Value{kind: kArray, items: items} }

// Obj is a JSON object; member order does not matter (JCS sorts).
func Obj(members ...Member) Value { return Value{kind: kObject, members: members} }

// FromYAML converts a decoded YAML node into the JSON value tree, refusing what
// the engine's JCS refuses (floats, unsafe integers).
func FromYAML(n *yaml.Node) (Value, error) {
	switch n.Kind {
	case yaml.DocumentNode:
		if len(n.Content) != 1 {
			return Value{}, fmt.Errorf("line %d: expected one document", n.Line)
		}
		return FromYAML(n.Content[0])
	case yaml.AliasNode:
		return FromYAML(n.Alias)
	case yaml.SequenceNode:
		items := make([]Value, 0, len(n.Content))
		for _, c := range n.Content {
			v, err := FromYAML(c)
			if err != nil {
				return Value{}, err
			}
			items = append(items, v)
		}
		return Arr(items), nil
	case yaml.MappingNode:
		members := make([]Member, 0, len(n.Content)/2)
		for i := 0; i+1 < len(n.Content); i += 2 {
			k := n.Content[i]
			if k.Kind != yaml.ScalarNode || k.ShortTag() != "!!str" {
				return Value{}, fmt.Errorf("line %d: mapping key %q is not a string", k.Line, k.Value)
			}
			v, err := FromYAML(n.Content[i+1])
			if err != nil {
				return Value{}, err
			}
			members = append(members, Member{Key: k.Value, Val: v})
		}
		return Obj(members...), nil
	case yaml.ScalarNode:
		return scalarFromYAML(n)
	}
	return Value{}, fmt.Errorf("line %d: unsupported YAML node kind %d", n.Line, n.Kind)
}

func scalarFromYAML(n *yaml.Node) (Value, error) {
	switch n.ShortTag() {
	case "!!str":
		return Str(n.Value), nil
	case "!!null":
		return Value{kind: kNull}, nil
	case "!!bool":
		b, err := strconv.ParseBool(strings.ToLower(n.Value))
		if err != nil {
			return Value{}, fmt.Errorf("line %d: boolean %q: %w", n.Line, n.Value, err)
		}
		return Value{kind: kBool, b: b}, nil
	case "!!int":
		i, err := strconv.ParseInt(n.Value, 10, 64)
		if err != nil {
			return Value{}, fmt.Errorf("line %d: integer %q is not plain decimal: %w", n.Line, n.Value, err)
		}
		return Value{kind: kInt, num: i}, nil
	case "!!float":
		return Value{}, fmt.Errorf("line %d: float %q in a digest-bearing field", n.Line, n.Value)
	}
	return Value{}, fmt.Errorf("line %d: unsupported scalar tag %s", n.Line, n.ShortTag())
}

// writeJCSString is RFC 8785 string serialization as agent_action_capsule
// implements it: the two-character escapes, \u00xx for other controls, and
// nothing else escaped.
func writeJCSString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '"':
			b.WriteString(`\"`)
		case r == '\\':
			b.WriteString(`\\`)
		case r == 0x08:
			b.WriteString(`\b`)
		case r == 0x09:
			b.WriteString(`\t`)
		case r == 0x0A:
			b.WriteString(`\n`)
		case r == 0x0C:
			b.WriteString(`\f`)
		case r == 0x0D:
			b.WriteString(`\r`)
		case r < 0x20:
			fmt.Fprintf(b, `\u%04x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
}

func utf16Less(a, b string) bool {
	return slices.Compare(utf16.Encode([]rune(a)), utf16.Encode([]rune(b))) < 0
}

func writeJCS(b *strings.Builder, v Value) error {
	switch v.kind {
	case kString:
		writeJCSString(b, v.str)
	case kNull:
		b.WriteString("null")
	case kBool:
		b.WriteString(strconv.FormatBool(v.b))
	case kInt:
		if v.num > maxSafeInteger || v.num < -maxSafeInteger {
			return fmt.Errorf("integer %d outside the safe range", v.num)
		}
		b.WriteString(strconv.FormatInt(v.num, 10))
	case kArray:
		b.WriteByte('[')
		for i, item := range v.items {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeJCS(b, item); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case kObject:
		members := slices.Clone(v.members)
		slices.SortFunc(members, func(x, y Member) int {
			switch {
			case utf16Less(x.Key, y.Key):
				return -1
			case utf16Less(y.Key, x.Key):
				return 1
			}
			return 0
		})
		b.WriteByte('{')
		for i, m := range members {
			if i > 0 {
				b.WriteByte(',')
			}
			if i > 0 && m.Key == members[i-1].Key {
				return fmt.Errorf("duplicate object key %q", m.Key)
			}
			writeJCSString(b, m.Key)
			b.WriteByte(':')
			if err := writeJCS(b, m.Val); err != nil {
				return err
			}
		}
		b.WriteByte('}')
	}
	return nil
}

// JCS is v's RFC 8785 serialization, as agent_action_capsule's jcs writes
// it; an integer outside the safe range has none.
func JCS(v Value) (string, error) {
	var b strings.Builder
	if err := writeJCS(&b, v); err != nil {
		return "", err
	}
	return b.String(), nil
}

// Digest is agent_action_capsule's JSON-DIGEST: lowercase-hex SHA-256 of
// the JCS bytes.
func Digest(v Value) (string, error) {
	s, err := JCS(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:]), nil
}

// M is an object member.
func M(key string, val Value) Member { return Member{Key: key, Val: val} }
