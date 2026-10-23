package guard

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// The task_authority check and the plan it reads, ported from capsule-engine
// 365eb92: guards/checks/task_authority.py, guards/plan.py
// (parse_plan_definition, PlanDefinition.canonical_dict) and
// guards/checks/plan_containment.py.

// Plan ids and verbs (plan.py OUTCOME_ID_RE, ACTION_VERB_RE). Python's re.match
// lets $ match before one final newline, so each pattern allows one here.
var (
	outcomeIDRE  = regexp.MustCompile(`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*/[0-9]+\.[0-9]+\.[0-9]+\n?$`)
	actionVerbRE = regexp.MustCompile(`^[a-z][a-z0-9_]*\n?$`)
)

// planPrecondition gates one allowed action on a citation.
type planPrecondition struct {
	action, citing string
}

// plan is a parsed PlanDefinition. binding keeps its JSON as supplied: it
// is digested as is and only its subject is read.
type plan struct {
	outcomeID      string
	allowedActions []string
	preconditions  []planPrecondition
	binding        map[string]json.RawMessage
	bindingValue   canon.Value
	window         *string
}

// errPlan is a plan that does not parse; the message says why.
var errPlan = errors.New("plan")

func planError(format string, args ...any) error {
	return fmt.Errorf("%w: %s", errPlan, fmt.Sprintf(format, args...))
}

// isJSONNull is whether raw is absent or JSON null (Python's None).
func isJSONNull(raw json.RawMessage) bool {
	return raw == nil || string(bytes.TrimSpace(raw)) == "null"
}

// parsePlan is parse_plan_definition over decoded JSON.
func parsePlan(raw json.RawMessage) (plan, error) {
	var data map[string]json.RawMessage
	if json.Unmarshal(raw, &data) != nil || data == nil {
		return plan{}, planError("malformed_definition: plan definition must be a mapping")
	}
	var p plan
	if json.Unmarshal(data["outcome_id"], &p.outcomeID) != nil || !outcomeIDRE.MatchString(p.outcomeID) {
		return plan{}, planError("invalid_outcome_id: outcome_id must match '<namespace>[.<namespace>...]/<major>.<minor>.<patch>'")
	}
	var actions []json.RawMessage
	if json.Unmarshal(data["allowed_actions"], &actions) != nil || len(actions) == 0 {
		return plan{}, planError("empty_allowed_actions: allowed_actions must be a non-empty list of verbs")
	}
	for _, a := range actions {
		var verb string
		if json.Unmarshal(a, &verb) != nil || !actionVerbRE.MatchString(verb) {
			return plan{}, planError("invalid_action_verb: allowed_actions entry %s must be a lowercase_snake_case verb", a)
		}
		if slices.Contains(p.allowedActions, verb) {
			return plan{}, planError("duplicate_allowed_action: allowed_actions lists %s more than once", verb)
		}
		p.allowedActions = append(p.allowedActions, verb)
	}
	if raw, ok := data["preconditions"]; ok {
		var entries []json.RawMessage
		if json.Unmarshal(raw, &entries) != nil || entries == nil {
			return plan{}, planError("malformed_definition: preconditions must be a list")
		}
		for _, e := range entries {
			var entry map[string]json.RawMessage
			var pc planPrecondition
			if json.Unmarshal(e, &entry) != nil || entry == nil || json.Unmarshal(entry["action"], &pc.action) != nil ||
				json.Unmarshal(entry["citing"], &pc.citing) != nil || isJSONNull(entry["action"]) || isJSONNull(entry["citing"]) {
				return plan{}, planError("invalid_precondition: precondition %s must be a mapping with string 'action' and 'citing'", e)
			}
			if !slices.Contains(p.allowedActions, pc.action) {
				return plan{}, planError("precondition_action_not_allowed: precondition gates %s, which is not in allowed_actions", pc.action)
			}
			p.preconditions = append(p.preconditions, pc)
		}
	}
	p.binding = map[string]json.RawMessage{}
	p.bindingValue = canon.Obj()
	if raw, ok := data["binding"]; ok {
		if json.Unmarshal(raw, &p.binding) != nil || p.binding == nil {
			return plan{}, planError("malformed_definition: binding must be a mapping")
		}
		v, err := canon.FromJSON(raw)
		if err != nil {
			return plan{}, planError("malformed_definition: binding: %v", err)
		}
		p.bindingValue = v
	}
	if raw := data["window"]; !isJSONNull(raw) {
		var w string
		if json.Unmarshal(raw, &w) != nil {
			return plan{}, planError("malformed_definition: window must be a string or omitted")
		}
		p.window = &w
	}
	return p, nil
}

// digest is PlanDefinition.definition_digest: SHA-256 over the JCS bytes of
// canonical_dict (window omitted when unset).
func (p plan) digest() (string, error) {
	pcs := make([]canon.Value, len(p.preconditions))
	for i, pc := range p.preconditions {
		pcs[i] = canon.Obj(canon.M("action", canon.Str(pc.action)), canon.M("citing", canon.Str(pc.citing)))
	}
	members := []canon.Member{canon.M("outcome_id", canon.Str(p.outcomeID)), canon.M("allowed_actions", canon.Strs(p.allowedActions)),
		canon.M("preconditions", canon.Arr(pcs)), canon.M("binding", p.bindingValue)}
	if p.window != nil {
		members = append(members, canon.M("window", canon.Str(*p.window)))
	}
	return canon.Digest(canon.Obj(members...))
}

// contains is check_plan_containment's result for a plan that is bound:
// the verb must be an allowed action, the action's target must equal a
// bound subject, and a verb with a precondition must cite a mandate. This
// port refuses an action that cites one (see Engine.check), so a gated
// verb is always uncited here. The reason is set on a fail.
func (p plan) contains(a Action) (Result, string) {
	if !slices.Contains(p.allowedActions, a.Verb) {
		return Fail, fmt.Sprintf("%s is a departure from plan %s: not in its allowed action set", a.Verb, p.outcomeID)
	}
	if subject, ok := p.binding["subject"]; ok && !isJSONNull(subject) {
		var s string
		if json.Unmarshal(subject, &s) != nil || a.Target == nil || *a.Target != s {
			target := strOrNone(a.Target)
			// A payee keyed per profile links the profile's deals: never in
			// a reason (plan_containment.py at capsule-engine 4ac9fb0).
			if strings.HasPrefix(target, ProfileTargetPrefix) {
				target = "another payee"
			}
			return Fail, fmt.Sprintf("%s is a departure from plan %s: plan binds subject=%s, action target is %s", a.Verb, p.outcomeID, subject, target)
		}
	}
	for _, pc := range p.preconditions {
		if pc.action == a.Verb {
			if a.CitedMandateCapsuleID == nil {
				return Fail, fmt.Sprintf("%s is a departure from plan %s: its precondition requires citing %s, and no citation was given", a.Verb, p.outcomeID, pc.citing)
			}
			break
		}
	}
	return Pass, ""
}

func strOrNone(s *string) string {
	if s == nil {
		return "None"
	}
	return *s
}

// planPath is where the plan sits inside the sealed record
// (task_authority.py PLAN_PATH).
const planPath = "body"

// checkTaskAuthority: the action is inside the task authority it cites
// (task_authority.py at 365eb92). task_authority_ref is the SHA-256 of the
// whole sealed record's JCS bytes; the supplied record is read only when
// this port's own digest of it equals the reference, never on a binding
// the producer asserts. The plan at body then decides. A missing record, a
// record the reference does not bind, no plan at body and a plan that
// does not parse are one case: n/a in scope naming task_authority_record,
// with the reason saying which.
func checkTaskAuthority(a Action, record json.RawMessage, c classConfig) (out Constraint) {
	const id, method, input = "task_authority", "bound_plan_containment_v0", "task_authority_record"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "task_authority_ref")
	if field, why := bindTaskAuthority(a, record); field != "" {
		c := missingField(id, method, field)
		c.pending = why
		return c
	}
	unread := func(reason string) Constraint {
		c := missingField(id, method, input)
		c.pending = reason + "; the task's bounds could not be read"
		return c
	}
	var top map[string]json.RawMessage
	var body json.RawMessage
	ok := false
	if json.Unmarshal(record, &top) == nil {
		body, ok = top[planPath]
	}
	if !ok {
		return unread("the task-authority record carries no plan at " + planPath)
	}
	p, err := parsePlan(body)
	if err != nil {
		return unread(fmt.Sprintf("the plan in the task-authority record does not parse (%v)", err))
	}
	planDigest, err := p.digest()
	if err != nil {
		return unread(fmt.Sprintf("the plan in the task-authority record does not parse (%v)", err))
	}
	result, why := p.contains(a)
	evidence := canon.Obj(canon.M("task_authority_ref", canon.Str(*a.TaskAuthorityRef)), canon.M("plan_digest", canon.Str(planDigest)),
		canon.M("containment", canon.Str(string(result))))
	out = decided(id, method, result, evidence)
	if result == Fail {
		out.failNote = "the action is not inside the task authority it cites: " + why
	}
	return out
}

// bindTaskAuthority is task_authority.py bind_task_authority_record
// (ba76a97): record is read only once this port's own digest of it equals
// the action's task_authority_ref. When it is not bound, field is the input
// an in-scope n/a names (task_authority_ref, or task_authority_record) and
// why says what was wrong; field is "" when record is bound.
func bindTaskAuthority(a Action, record json.RawMessage) (field, why string) {
	const input, unread = "task_authority_record", "; the task's bounds could not be read"
	if a.TaskAuthorityRef == nil {
		return "task_authority_ref", ""
	}
	if record == nil {
		return input, "no task-authority record was supplied" + unread
	}
	v, err := canon.FromJSON(record)
	if err != nil {
		return input, fmt.Sprintf("the supplied task-authority record has no digest (%v)", err) + unread
	}
	got, err := canon.Digest(v)
	if err != nil {
		return input, fmt.Sprintf("the supplied task-authority record has no digest (%v)", err) + unread
	}
	if got != *a.TaskAuthorityRef {
		return input, "ref mismatch: the supplied task-authority record is not the record task_authority_ref names" + unread
	}
	return "", ""
}

// disclosedTaskAuthority is the disclosed task-authority/v0 record whose
// digest is ref, or nil. A record with no digest (a float, an unsafe
// integer) is left out: no reference can name it.
func disclosedTaskAuthority(disclosed map[string]json.RawMessage, ref *string) json.RawMessage {
	if ref == nil {
		return nil
	}
	for _, shown := range disclosed {
		if typeOf(objectOf(shown)) != taskAuthorityType {
			continue
		}
		v, err := canon.FromJSON(shown)
		if err != nil {
			continue
		}
		if got, err := canon.Digest(v); err == nil && got == *ref {
			return shown
		}
	}
	return nil
}
