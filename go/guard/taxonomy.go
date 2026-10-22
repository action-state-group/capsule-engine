package guard

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"slices"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// actionClass is one row of capsule-engine's action-class table
// (guards/action_taxonomy.json, read by guards/classes.py).
type actionClass struct {
	Name            string   `json:"name"`
	TriggerClass    *string  `json:"trigger_class"`
	Consequential   *bool    `json:"consequential"`
	FailOpenAllowed *bool    `json:"fail_open_allowed"`
	ApproverRole    *string  `json:"approver_role"`
	LegacyAliases   []string `json:"legacy_aliases"`
}

// taxonomy is the table's classes by canonical name, and legacy name ->
// canonical name.
type taxonomy struct {
	version string
	classes map[string]actionClass
	aliases map[string]string
}

// rowKeys are the keys every row declares (classes.py _ROW_KEYS): a row
// cannot silently inherit a policy.
var rowKeys = []string{"name", "trigger_class", "consequential", "fail_open_allowed", "approver_role", "legacy_aliases"}

// loadTaxonomy parses the table as classes.py load_taxonomy_table does,
// for the parts this port reads: every row declares every key and no other,
// names and aliases are unique, and an alias collides with no name.
func loadTaxonomy(raw []byte) (taxonomy, error) {
	var doc struct {
		Version string            `json:"taxonomy_version"`
		Actions []json.RawMessage `json:"actions"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	if err := dec.Decode(&doc); err != nil {
		return taxonomy{}, fmt.Errorf("action taxonomy: %w", err)
	}
	if doc.Version == "" || len(doc.Actions) == 0 {
		return taxonomy{}, errors.New("action taxonomy: no taxonomy_version or no actions")
	}
	t := taxonomy{version: doc.Version, classes: map[string]actionClass{}, aliases: map[string]string{}}
	for i, rawRow := range doc.Actions {
		var keys map[string]json.RawMessage
		if err := json.Unmarshal(rawRow, &keys); err != nil {
			return taxonomy{}, fmt.Errorf("action taxonomy actions[%d]: %w", i, err)
		}
		if len(keys) != len(rowKeys) {
			return taxonomy{}, fmt.Errorf("action taxonomy actions[%d] declares %d keys, want exactly %v", i, len(keys), rowKeys)
		}
		for _, k := range rowKeys {
			if _, ok := keys[k]; !ok {
				return taxonomy{}, fmt.Errorf("action taxonomy actions[%d] does not declare %q", i, k)
			}
		}
		var row actionClass
		if err := json.Unmarshal(rawRow, &row); err != nil {
			return taxonomy{}, fmt.Errorf("action taxonomy actions[%d]: %w", i, err)
		}
		if row.Name == "" || row.Consequential == nil || row.FailOpenAllowed == nil || row.LegacyAliases == nil {
			return taxonomy{}, fmt.Errorf("action taxonomy actions[%d] is under-declared", i)
		}
		if _, dup := t.classes[row.Name]; dup {
			return taxonomy{}, fmt.Errorf("action class %q declared more than once", row.Name)
		}
		t.classes[row.Name] = row
	}
	for _, name := range sortedClassNames(t.classes) {
		for _, alias := range t.classes[name].LegacyAliases {
			if _, isName := t.classes[alias]; isName {
				return taxonomy{}, fmt.Errorf("legacy alias %q on %q collides with another name", alias, name)
			}
			if _, isAlias := t.aliases[alias]; isAlias {
				return taxonomy{}, fmt.Errorf("legacy alias %q on %q collides with another name", alias, name)
			}
			t.aliases[alias] = name
		}
	}
	return t, nil
}

func sortedClassNames(m map[string]actionClass) []string {
	names := make([]string, 0, len(m))
	for n := range m {
		names = append(names, n)
	}
	slices.Sort(names)
	return names
}

// canonical is classes.py resolve's name: a legacy name maps to its row's
// name; any other name is kept as written (caps.py _canonical).
func (t taxonomy) canonical(class string) string {
	if c, ok := t.aliases[class]; ok {
		return c
	}
	return class
}

// resolve is classes.py resolve: the canonical row for a canonical or legacy
// name, or false.
func (t taxonomy) resolve(class string) (actionClass, bool) {
	row, ok := t.classes[t.canonical(class)]
	return row, ok
}

// declaredTriggers are the trigger classes some row declares
// (action_class_gate.py _DECLARED_TRIGGERS; CHANGE is derived from state and
// no row declares it).
func (t taxonomy) declaredTriggers() map[string]bool {
	out := map[string]bool{}
	for _, row := range t.classes {
		if row.TriggerClass != nil {
			out[*row.TriggerClass] = true
		}
	}
	return out
}

// approver is classify(class).approver_role: an absent or unknown class is
// the fail-closed default, which has none.
func (t taxonomy) approver(class string) (string, bool) {
	row, ok := t.classes[t.canonical(class)]
	if !ok || row.ApproverRole == nil {
		return "", false
	}
	return *row.ApproverRole, true
}

// resolveLimits is caps.py resolve_caps_minor: the limits keyed by
// canonical class, refusing a legacy and a canonical key for one class that
// carry different limits.
func (t taxonomy) resolveLimits(limits map[string]int64) (map[string]int64, error) {
	out := map[string]int64{}
	for _, name := range sortedKeys(limits) {
		c := t.canonical(name)
		if prev, ok := out[c]; ok && prev != limits[name] {
			return nil, fmt.Errorf("limits set two values for %q (%d and %d) under a legacy and a canonical name", c, prev, limits[name])
		}
		out[c] = limits[name]
	}
	return out, nil
}

// engineTaxonomy is the vendored table.
func engineTaxonomy() (taxonomy, error) {
	return loadTaxonomy(packs.ActionTaxonomy)
}

// sameClassesAs are, by the engine's taxonomy version, the earlier versions
// whose class names are exactly its own. capsule-engine's action_taxonomy.json
// version 2 (2521ee6), version 3 (a5e9e30), version 4 (5300e0b), version 5
// (e5ec2c1) and version 6 (7ae6b95) name the same 19 classes, in the same
// order, and differ only in approver_role: 3 gives booking.create,
// booking.modify, money.purchase and money.subscription account_holder, 4
// also booking.cancel, data.delete, communication.publish and
// disclosure.personal, 5 also communication.send, and 6 also
// marketplace.offer, marketplace.sale and agreement.accept. A history act
// sealed under one of these is counted as the engine counts its own
// (capsule-cli stamps new records 6; those sealed before keep 2, 3, 4 or 5).
// The record being checked must still carry the engine's version: its
// approver comes from the table.
var sameClassesAs = map[string][]string{"6": {"2", "3", "4", "5"}}

// sameClasses is whether a history act sealed under version v names its
// class from a table with the engine's class names.
func (t taxonomy) sameClasses(v string) bool {
	return v == t.version || slices.Contains(sameClassesAs[t.version], v)
}
