package guard

import (
	"errors"
	"fmt"
	"slices"
	"strings"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// action_class_gate/1.0.0 (capsule-engine guards/checks/action_class_gate.py
// and guards/wickets/catalog_defs/action_class_gate.yaml, c7fc3ac): named
// selectors over the action-class table. It reads the action's class,
// resolved to its canonical row, and no fold. action_class_gate/1.1.0
// (7339028) lets a selector set except_recipient_roles: it then does not
// match an action whose recipient_role is one of them (personal_disclosure
// leaves a disclosure to a platform to recipient_role), reading
// recipient_role only for such a selector. action_class_gate/1.2.0
// (b5d2c77, capsule-engine #203) adds except_repeat_disclosures and
// except_recipient_told_before, read in that order after the role: a
// selector then also reads disclosed_classes and repeat_disclosures, or
// recipient_told_before, when it reaches that exception.

const gateMethod = "taxonomy_selector_v0"

// gateConfig keeps the selectors undecoded: they are parsed on every
// decision, so a bad selector fails the check that reads it, never the
// load (action_class_gate.py docstring).
type gateConfig struct {
	Selectors yaml.Node `yaml:"selectors"`
}

// gateSelector is one parsed selector; a nil slice or pointer is a key the
// selector does not set.
type gateSelector struct {
	actionClasses, triggerClasses []string
	consequential                 *bool
	onMatch                       string
	// exceptRoles narrows the selector; it selects nothing on its own.
	exceptRoles []string
	// exceptRepeat and exceptTold (action_class_gate/1.2.0, capsule-engine
	// #203) narrow it by the producer's telling memory: an action every one
	// of whose disclosed classes is in its repeat_disclosures, or whose
	// recipient_told_before is true, is not matched.
	exceptRepeat, exceptTold bool
}

// errBadSelectors marks a wicket whose selectors the action-class table
// cannot resolve (action_class_gate.py raises ValueError).
var errBadSelectors = errors.New("action_class_gate selectors")

func badSelector(format string, args ...any) error {
	return fmt.Errorf("%w: %s", errBadSelectors, fmt.Sprintf(format, args...))
}

// parseSelectors is action_class_gate.py parse_selectors: every selector is
// a mapping that sets at least one of action_classes, trigger_classes and
// consequential, and on_match fail or pass; every class it names is a
// canonical row and every trigger class one some row declares.
func parseSelectors(node yaml.Node, tax taxonomy) (map[string]gateSelector, error) {
	if node.Kind != yaml.MappingNode || len(node.Content) == 0 {
		return nil, badSelector("selectors must be a non-empty mapping of selector id to selector")
	}
	triggers := tax.declaredTriggers()
	out := map[string]gateSelector{}
	for i := 0; i < len(node.Content); i += 2 {
		k, v := node.Content[i], node.Content[i+1]
		sid := k.Value
		if k.Kind != yaml.ScalarNode || k.ShortTag() != "!!str" || sid == "" {
			return nil, badSelector("selector id %q must be a non-empty string", sid)
		}
		if v.Kind != yaml.MappingNode {
			return nil, badSelector("selector %q must be a mapping", sid)
		}
		var sel gateSelector
		selects := false
		for j := 0; j < len(v.Content); j += 2 {
			key, val := v.Content[j].Value, v.Content[j+1]
			switch key {
			case "action_classes", "trigger_classes", "except_recipient_roles":
				var names []string
				if val.Kind != yaml.SequenceNode || len(val.Content) == 0 {
					return nil, badSelector("selector %q %s must be a non-empty list of names", sid, key)
				}
				for _, n := range val.Content {
					if n.Kind != yaml.ScalarNode || n.ShortTag() != "!!str" {
						return nil, badSelector("selector %q %s must be a non-empty list of names", sid, key)
					}
					names = append(names, n.Value)
				}
				switch key {
				case "action_classes":
					sel.actionClasses, selects = names, true
				case "trigger_classes":
					sel.triggerClasses, selects = names, true
				default:
					sel.exceptRoles = names
				}
			case "consequential":
				if val.Kind != yaml.ScalarNode || val.ShortTag() != "!!bool" {
					return nil, badSelector("selector %q consequential must be true or false", sid)
				}
				b := val.Value == "true"
				sel.consequential, selects = &b, true
			case "except_repeat_disclosures", "except_recipient_told_before":
				// action_class_gate.py: true when set.
				var on bool
				if val.Kind != yaml.ScalarNode || val.ShortTag() != "!!bool" || val.Decode(&on) != nil || !on {
					return nil, badSelector("selector %q %s must be true when set", sid, key)
				}
				if key == "except_repeat_disclosures" {
					sel.exceptRepeat = true
				} else {
					sel.exceptTold = true
				}
			case "on_match":
				sel.onMatch = val.Value
			default:
				return nil, badSelector("selector %q carries unknown key %q", sid, key)
			}
		}
		if !selects {
			return nil, badSelector("selector %q selects nothing; set one of action_classes, consequential, trigger_classes", sid)
		}
		if sel.onMatch != "fail" && sel.onMatch != "pass" {
			return nil, badSelector("selector %q on_match must be one of fail, pass", sid)
		}
		for _, n := range sel.actionClasses {
			if _, ok := tax.classes[n]; !ok {
				return nil, badSelector("selector %q names %q, not a canonical class in taxonomy %s", sid, n, tax.version)
			}
		}
		for _, n := range sel.triggerClasses {
			if !triggers[n] {
				return nil, badSelector("selector %q names trigger class %q; no taxonomy row declares it", sid, n)
			}
		}
		out[sid] = sel
	}
	return out, nil
}

func (s gateSelector) matches(row actionClass) bool {
	if s.actionClasses != nil && !slices.Contains(s.actionClasses, row.Name) {
		return false
	}
	if s.triggerClasses != nil && (row.TriggerClass == nil || !slices.Contains(s.triggerClasses, *row.TriggerClass)) {
		return false
	}
	return s.consequential == nil || *s.consequential == *row.Consequential
}

// checkActionClassGate is check_action_class_gate: an action with no class,
// or a class with no row, fails closed; otherwise fail when a fail selector
// matches, pass when only pass selectors match, out of scope when none does.
// A bad selector is an error from this decision.
func checkActionClassGate(a Action, node yaml.Node, tax taxonomy) (c Constraint, err error) {
	const id = "action_class_gate"
	// The class only: the taxonomy version is the engine's own, never the
	// action's taxonomy_version.
	reads := []string{"action_class"}
	defer func() { c.reads = reads }()
	selectors, err := parseSelectors(node, tax)
	if err != nil {
		return Constraint{}, err
	}
	var row actionClass
	ok := false
	if a.ActionClass != nil {
		row, ok = tax.resolve(*a.ActionClass)
	}
	if !ok {
		class := canon.Null()
		if a.ActionClass != nil {
			class = canon.Str(*a.ActionClass)
		}
		return decided(id, gateMethod, Fail, canon.Obj(canon.M("action_class", class), canon.M("in_taxonomy", canon.Bool(false)),
			canon.M("taxonomy_version", canon.Str(tax.version)))), nil
	}
	var matched []string
	named := false
	why := map[string]bool{}
	for sid, sel := range selectors {
		if !sel.matches(row) {
			continue
		}
		named = true
		w, consulted := sel.excepted(a)
		reads = append(reads, consulted...)
		if w != "" {
			why[w] = true
			continue
		}
		matched = append(matched, sid)
	}
	slices.Sort(reads)
	reads = slices.Compact(reads)
	if len(matched) == 0 {
		out := outOfScope(id, gateMethod)
		if named {
			out.scopeNote = leftOutReason(row.Name, a.RecipientRole, why)
		}
		return out, nil
	}
	onMatch := make(map[string]string, len(selectors))
	for sid, sel := range selectors {
		onMatch[sid] = sel.onMatch
	}
	defer func() { c.gate = &gateMatch{class: row.Name, matched: matched, onMatch: onMatch} }()
	slices.Sort(matched)
	trigger := canon.Null()
	if row.TriggerClass != nil {
		trigger = canon.Str(*row.TriggerClass)
	}
	evidence := canon.Obj(canon.M("action_class", canon.Str(row.Name)), canon.M("trigger_class", trigger),
		canon.M("consequential", canon.Bool(*row.Consequential)), canon.M("taxonomy_version", canon.Str(tax.version)),
		canon.M("matched_selectors", canon.Strs(matched)))
	r := Pass
	for _, sid := range matched {
		if selectors[sid].onMatch == "fail" {
			r = Fail
		}
	}
	return decided(id, gateMethod, r, evidence), nil
}

// Why a matching selector leaves an action out (action_class_gate.py
// _excepted): fixed phrases, never a value the action carries but its role.
const (
	exceptedRole     = "role"
	exceptedRepeat   = "every class it discloses was told to this recipient before"
	exceptedToldOnce = "this recipient was told something before"
)

// excepted is why s does not match a although its class keys do, or "",
// and the inputs it consulted to say so: the recipient's role first, then a
// repeat of classes already told, then a recipient told anything before,
// each read only when the selector sets its key and gets that far. A
// missing input takes no exception.
func (s gateSelector) excepted(a Action) (string, []string) {
	var read []string
	if s.exceptRoles != nil {
		read = append(read, "recipient_role")
		if a.RecipientRole != nil && slices.Contains(s.exceptRoles, *a.RecipientRole) {
			return exceptedRole, read
		}
	}
	if s.exceptRepeat {
		read = append(read, "disclosed_classes", "repeat_disclosures")
		if allRepeated(a) {
			return exceptedRepeat, read
		}
	}
	if s.exceptTold {
		read = append(read, "recipient_told_before")
		if a.RecipientToldBefore != nil && *a.RecipientToldBefore {
			return exceptedToldOnce, read
		}
	}
	return "", read
}

// allRepeated is action_class_gate.py _all_repeated: every class a discloses
// was told to its recipient before. One naming no class, or carrying no
// repeat list, is not a repeat.
func allRepeated(a Action) bool {
	if len(a.DisclosedClasses) == 0 || a.RepeatDisclosures == nil {
		return false
	}
	for _, c := range a.DisclosedClasses {
		if !slices.Contains(a.RepeatDisclosures, c) {
			return false
		}
	}
	return true
}

// leftOutReason is the n/a reason when every selector naming an action's
// class left it out: 1.1.0's, byte for byte, when each did so for its role,
// else every reason, the role named, sorted and joined by "; ".
func leftOutReason(class string, role *string, why map[string]bool) string {
	named := "None"
	if role != nil {
		named = pyRepr(*role)
	}
	if len(why) == 1 && why[exceptedRole] {
		return fmt.Sprintf("the selectors naming action class %s leave out recipient_role %s", pyRepr(class), named)
	}
	var phrases []string
	for w := range why {
		if w == exceptedRole {
			w = "recipient_role " + named
		}
		phrases = append(phrases, w)
	}
	slices.Sort(phrases)
	return fmt.Sprintf("the selectors naming action class %s leave this action out: %s", pyRepr(class), strings.Join(phrases, "; "))
}
