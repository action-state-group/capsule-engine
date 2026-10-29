package guard

import (
	"fmt"
	"reflect"
	"strings"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// required_fields is capsule-engine guards/checks/required_fields.py
// (capsule-engine#263): an action whose verb names one of the pack's action
// types (action_semantics) carries every field that type requires. With all
// of them set (not nil, "" or an empty list) it passes; with one missing it
// is n/a, in scope, naming the first missing field in the pack's order, and
// fails closed. An action whose verb names no type gets no constraint. It is
// appended after the configured checks and never held by a taxonomy
// mismatch (neverHeld).

const requiredFieldsMethod = "action_semantics_v0"

// actionFieldIndex is each Action field by its normalized name (its json
// tag): how required_fields reads a field the pack names.
var actionFieldIndex = func() map[string]int {
	out := map[string]int{}
	t := reflect.TypeOf(Action{})
	for i := range t.NumField() {
		name, _, _ := strings.Cut(t.Field(i).Tag.Get("json"), ",")
		if name != "" && name != "-" {
			out[name] = i
		}
	}
	return out
}()

// requiredFieldsOf is packs/install.py required_fields: each action type of
// p's action semantics and the fields it requires, refusing a field this
// port's Action does not carry.
func requiredFieldsOf(p pack.Pack) (map[string][]string, error) {
	out := map[string][]string{}
	for _, s := range p.ActionSemantics {
		for _, f := range s.RequiredFields {
			if _, ok := actionFieldIndex[f]; !ok {
				return nil, fmt.Errorf("action type %s requires %q, which this port's Action does not carry", s.ActionType, f)
			}
		}
		out[s.ActionType] = s.RequiredFields
	}
	return out, nil
}

// fieldSet is required_fields.py _missing, negated.
func fieldSet(a Action, name string) bool {
	v := reflect.ValueOf(a).Field(actionFieldIndex[name])
	switch v.Kind() {
	case reflect.Pointer:
		if v.IsNil() {
			return false
		}
		if e := v.Elem(); e.Kind() == reflect.String {
			return e.String() != ""
		}
		return true
	case reflect.Slice, reflect.Map, reflect.String:
		return v.Len() > 0
	}
	return true
}

// checkRequiredFields is required_fields.py check_required_fields; ok is
// false when a's verb names no action type in required.
func checkRequiredFields(a Action, required map[string][]string) (out Constraint, ok bool) {
	const id = "required_fields"
	fields, ok := required[a.Verb]
	if !ok {
		return Constraint{}, false
	}
	for _, f := range fields {
		if !fieldSet(a, f) {
			out = missingField(id, requiredFieldsMethod, f)
			out.failsClosed = true
			out.reads = []string{"verb", f}
			return out, true
		}
	}
	names := make([]canon.Value, len(fields))
	for i, f := range fields {
		names[i] = canon.Str(f)
	}
	out = decided(id, requiredFieldsMethod, Pass, canon.Obj(
		canon.M("action_type", canon.Str(a.Verb)), canon.M("required_fields", canon.Arr(names))))
	out.reads = append([]string{"verb"}, fields...)
	return out, true
}
