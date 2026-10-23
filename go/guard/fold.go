package guard

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// fold is the subset of capsule-engine's fold engine (folds/engine.py,
// c7fc3ac) the cited folds use: declared reads, with a default where one is
// declared; eq and ne filters on string, bool and null values, and in over a
// list of them (97a2aff, counterparty.seen_before/3.0.0); a key; an
// optional rolling window; and the sum or count reducer. Any other shape is
// refused when the engine is built rather than approximated.
type fold struct {
	id, digest string
	reads      []foldRead
	filters    []foldFilter
	key        string
	window     time.Duration // 0: no window
	windowSpec string        // the definition's duration, e.g. "7d"
	reducer    string        // sum | count
	sumField   string
	// reversal is the fold's reversal clause (spend.weekly/3.0.0), or nil.
	reversal *pack.FoldReversal
}

// reversalSummary is engine.py ReversalSummary: how many reversal records
// took back a positive amount from a charge they cite, the total taken
// back, and how many took back nothing.
type reversalSummary struct {
	linkedCount, linkedMinor, unlinkedCount int64
}

type foldRead struct {
	path       string
	hasDefault bool
	def        value
}

type foldFilter struct {
	field, op string
	value     value
	// values is the list an in filter matches against.
	values []value
}

// value is one field value as the fold engine sees it: absent, null, a
// string, a bool or an integer.
type value struct {
	kind valueKind
	s    string
	b    bool
	i    int64
}

type valueKind int

const (
	absent valueKind = iota
	null
	str
	boolean
	integer
)

func strValue(p *string) value {
	if p == nil {
		return value{}
	}
	return value{kind: str, s: *p}
}

func nonEmpty(s string) value {
	if s == "" {
		return value{}
	}
	return value{kind: str, s: s}
}

// recordValue is folds/paths.py get_path for the record fields this port
// carries. A field the record does not carry is absent.
func recordValue(r Record, path string) value {
	switch path {
	case "developer":
		return nonEmpty(r.Developer)
	case "operator":
		return nonEmpty(r.Operator)
	case "timestamp":
		return nonEmpty(r.Timestamp)
	case "disposition.decision":
		return strValue(r.Decision)
	case "asg_payload.target":
		return strValue(r.Target)
	case "asg_payload.action_class":
		return strValue(r.ActionClass)
	case "asg_payload.representation_class":
		return strValue(r.RepresentationClass)
	case "asg_payload.stated.class", "asg_payload.stated.source_kind":
		if r.Stated == nil {
			return value{}
		}
		if path == "asg_payload.stated.class" {
			return nonEmpty(r.Stated.Class)
		}
		return nonEmpty(r.Stated.SourceKind)
	case "chain.parent_capsule_id":
		return strValue(r.ChainParent)
	case "chain.relation":
		return strValue(r.ChainRelation)
	case "asg_payload.checkpoint.dry_run":
		if r.DryRun == nil {
			return value{}
		}
		return value{kind: boolean, b: *r.DryRun}
	case "asg_payload.amount_minor":
		if r.AmountMinor == nil {
			return value{}
		}
		return value{kind: integer, i: *r.AmountMinor}
	case "asg_payload.live_history":
		// spend.weekly/3.1.0 leaves out the engine's own copy of a history
		// act (report/live_history.py marks it "disposition"), whose
		// executed record already counts it. This port writes neither: a
		// history act is read as it is sealed, and the executed acts are
		// counted outside the fold (outsideTheFold).
		return strValue(r.LiveHistory)
	}
	return value{}
}

var recordFields = map[string]bool{"developer": true, "operator": true, "timestamp": true, "disposition.decision": true,
	"asg_payload.target": true, "asg_payload.action_class": true, "chain.parent_capsule_id": true, "chain.relation": true,
	"asg_payload.checkpoint.dry_run": true, "asg_payload.amount_minor": true, "asg_payload.representation_class": true,
	"asg_payload.stated.class": true, "asg_payload.stated.source_kind": true, "asg_payload.live_history": true}

// scalar is a YAML scalar as a fold value: a string, a bool or null.
func scalar(n yaml.Node) (value, bool) {
	if n.Kind != yaml.ScalarNode {
		return value{}, false
	}
	switch n.ShortTag() {
	case "!!str":
		return value{kind: str, s: n.Value}, true
	case "!!bool":
		return value{kind: boolean, b: n.Value == "true"}, true
	case "!!null":
		return value{kind: null}, true
	}
	return value{}, false
}

// scalars is a YAML sequence of scalars, as an in filter's list (the engine
// refuses an in filter whose value is not a list, definition.py).
func scalars(n yaml.Node) ([]value, bool) {
	if n.Kind != yaml.SequenceNode {
		return nil, false
	}
	out := make([]value, 0, len(n.Content))
	for _, item := range n.Content {
		v, ok := scalar(*item)
		if !ok {
			return nil, false
		}
		out = append(out, v)
	}
	return out, true
}

var durationRE = regexp.MustCompile(`^(\d+)([smhd])$`)

func newFold(f pack.Fold) (fold, error) {
	fl := fold{id: f.ID, digest: f.Digest, key: f.Key, reducer: f.Reducer, sumField: f.ReduceField, reversal: f.Reversal}
	refuse := func(why string) (fold, error) {
		return fold{}, fmt.Errorf("fold %s: %s is not supported by this port", f.ID, why)
	}
	declared := map[string]bool{}
	for _, r := range f.Reads {
		if !recordFields[r.Path] {
			return refuse("read " + r.Path)
		}
		rd := foldRead{path: r.Path, hasDefault: r.HasDefault}
		if r.HasDefault {
			v, ok := scalar(r.Default)
			if !ok {
				return refuse("the default of " + r.Path)
			}
			rd.def = v
		}
		fl.reads = append(fl.reads, rd)
		declared[r.Path] = true
	}
	for _, c := range f.Filter {
		if !declared[c.Field] {
			return refuse("filter " + c.Field + " " + c.Op)
		}
		if c.Op == "in" {
			vs, ok := scalars(c.Value)
			if !ok {
				return refuse("filter " + c.Field + " " + c.Op)
			}
			fl.filters = append(fl.filters, foldFilter{field: c.Field, op: c.Op, values: vs})
			continue
		}
		v, ok := scalar(c.Value)
		if (c.Op != "eq" && c.Op != "ne") || !ok {
			return refuse("filter " + c.Field + " " + c.Op)
		}
		fl.filters = append(fl.filters, foldFilter{field: c.Field, op: c.Op, value: v})
	}
	switch {
	case f.Reducer == "sum" && f.ReduceField == "asg_payload.amount_minor" && declared[f.ReduceField]:
	case f.Reducer == "count" && f.ReduceField == "":
	default:
		return refuse("reducer " + f.Reducer + " over " + f.ReduceField)
	}
	if f.Key == "" || !declared[f.Key] {
		return refuse("key " + f.Key)
	}
	// definition.py _parse_reversal: a reversal takes back part of a sum,
	// over declared fields, under the one registered terminal relation.
	if r := f.Reversal; r != nil && (f.Reducer != "sum" || !declared[r.ClassField] || !declared[r.ParentField] ||
		!declared[r.RelationField] || len(r.Classes) == 0 || r.Relation != "supersedes") {
		return refuse("its reversal clause")
	}
	if f.WindowMode != "" || f.WindowDuration != "" {
		m := durationRE.FindStringSubmatch(f.WindowDuration)
		if f.WindowMode != "rolling" || m == nil || !declared["timestamp"] {
			return refuse("window " + f.WindowMode + " " + f.WindowDuration)
		}
		n, err := strconv.ParseInt(m[1], 10, 64)
		if err != nil {
			return refuse("window duration " + f.WindowDuration)
		}
		unit := map[string]time.Duration{"s": time.Second, "m": time.Minute, "h": time.Hour, "d": 24 * time.Hour}[m[2]]
		fl.window = time.Duration(n) * unit
		fl.windowSpec = f.WindowDuration
	}
	return fl, nil
}

// resolve is engine.py _resolve_reads: every declared read, a default
// standing in for an absent field; false when a field with no default is
// absent (the record is skipped).
func (f fold) resolve(r Record) (map[string]value, bool) {
	vals := make(map[string]value, len(f.reads))
	for _, rd := range f.reads {
		v := recordValue(r, rd.path)
		if v.kind == absent {
			if !rd.hasDefault {
				return nil, false
			}
			v = rd.def
		}
		vals[rd.path] = v
	}
	return vals, true
}

// disagreeing counts the records evaluate would read for keyValue in the
// window that carry a spend_minor differing from their amount_minor (or no
// amount_minor). The fold sums amount_minor, so on such a record (a refund
// or cancel body) its total may count a refund as spend.
func (f fold) disagreeing(records []Record, keyValue string, anchor time.Time) int {
	n := 0
	for _, r := range records {
		if r.SpendMinor == nil {
			continue
		}
		vals, ok := f.resolve(r)
		if !ok || !f.within(vals, anchor) || !f.passes(vals) {
			continue
		}
		if k := vals[f.key]; k.kind != str || k.s != keyValue {
			continue
		}
		if r.AmountMinor == nil || *r.AmountMinor != *r.SpendMinor {
			n++
		}
	}
	return n
}

// outsideTheFold is the acts carrying keyValue under the fold's key in its
// window as of anchor that moved money and that the fold never sums, as it
// sums only accepted acts, each with the spend it took, or nil when there
// are none: the unchecked acts (Record.Unchecked; ruling (a)) and the
// executed acts with no recorded decision whose spend can be read
// (Record.executedSpend; the G3 ruling). The fold's filters are not
// applied: neither has a decision to pass one. An unchecked act's spend is
// spend_minor when sealed, else amount_minor (0 when the money moved in, as
// DecodeHistoryAct bridges it); an executed act's is its sealed spend_minor.
//
// Each act is counted once: an act the guard accepted is the fold's, so a
// record of it carrying no decision (the same capsule_id, or the same
// action_id) adds nothing, and the same act given twice is counted once.
//
// Under a fold that counts executed acts (spend.weekly/3.1.0,
// countsExecuted), the acts are the records capsule-engine's live check
// writes for them (#138, report/replay.py ExecutedActs, read by caps.py):
// every act in a history is executed (history), so one sealed with a
// decision other than accept counts at its sealed spend as one with none
// does; an act that moved money in is a record of 0; and an act whose class
// is one the fold's reversal clause names (a cancel, a refund) takes nothing
// back, as it cites no charge, so it adds nothing and is counted in
// unlinked. unreadable is how many of them have a spend that cannot be read,
// and how many ledger records of such an act (unreadableExecuted) are in the
// window: with any, the total is not known (checkCaps). The acts are read in
// the engine's order (executedOrder), and a repeat of an act is dropped
// before the window is read, as ExecutedActs drops it. Under any other fold
// unlinked and unreadable are 0, and a history act's gap is windowCoverage's.
func (f fold) outsideTheFold(records []Record, keyValue string, anchor time.Time, history bool) (unchecked, executed *ActsInWindow, unlinked, unreadable int64) {
	executedActs := f.countsExecuted()
	counted := map[string]bool{}
	for _, r := range records {
		if r.accepted() {
			counted["capsule "+r.CapsuleID] = true
			if r.ActionID != "" {
				counted["action "+r.ActionID] = true
			}
		}
	}
	seen := func(r Record) bool {
		if counted["capsule "+r.CapsuleID] || (r.ActionID != "" && counted["action "+r.ActionID]) {
			return true
		}
		counted["capsule "+r.CapsuleID] = true
		if r.ActionID != "" {
			counted["action "+r.ActionID] = true
		}
		return false
	}
	for _, r := range executedOrder(records, executedActs && history) {
		if r.Stated != nil {
			// A claim the user's agent made is not an act: the engine writes
			// no spend record for it (live_history.py at #139's 08cd96d0:
			// only for an entry whose stated is None).
			continue
		}
		var spend int64
		into := &executed
		s, ok := r.executedSpend()
		if executedActs && history && r.Decision != nil && !r.accepted() {
			s, ok = r.sealedSpend()
		}
		moneyInAct := r.Direction != nil && *r.Direction == moneyIn
		undecided := r.Decision == nil || (executedActs && history && !r.accepted())
		unread := false
		switch {
		case r.Unchecked:
			into = &unchecked
			amount := r.AmountMinor
			if r.SpendMinor != nil {
				amount = r.SpendMinor
			}
			if amount != nil {
				spend = max(*amount, 0)
			}
		case ok:
			spend = s
		case executedActs && undecided && moneyInAct:
		case executedActs && history && undecided:
			unread = true
		case executedActs && r.unreadableExecuted():
			// Already the engine's own record, written once per act.
			if f.inWindowFor(r, keyValue, anchor) {
				unreadable++
			}
			continue
		default:
			continue
		}
		// report/replay.py ExecutedActs: the engine writes one record per
		// act before any window reads it, so under an executed-acts fold a
		// repeat is dropped whether or not either copy is in the window.
		if executedActs && seen(r) {
			continue
		}
		if !f.inWindowFor(r, keyValue, anchor) {
			continue
		}
		if !executedActs && seen(r) {
			continue
		}
		if unread {
			unreadable++
			continue
		}
		if executedActs {
			if f.reversal != nil && r.ActionClass != nil && slices.Contains(f.reversal.Classes, *r.ActionClass) {
				unlinked++
				continue
			}
			if moneyInAct && !r.Unchecked {
				continue
			}
		}
		if *into == nil {
			*into = &ActsInWindow{}
		}
		(*into).Count++
		(*into).Minor += spend
	}
	return unchecked, executed, unlinked, unreadable
}

// executedOrder is records in the order capsule-engine's live check writes
// its history (report/live_history.py): oldest first by timestamp, acts of
// the same instant in the order given, when ledgerOrder is set; records as
// given otherwise. Of two copies of one act, the engine counts the first it
// writes.
func executedOrder(records []Record, ledgerOrder bool) []Record {
	if !ledgerOrder {
		return records
	}
	out := slices.Clone(records)
	slices.SortStableFunc(out, func(a, b Record) int {
		ta, errA := parseInstant(a.Timestamp)
		tb, errB := parseInstant(b.Timestamp)
		if errA != nil || errB != nil {
			return 0
		}
		return ta.Compare(tb)
	})
	return out
}

// executedDecision is the disposition.decision of capsule-engine's record
// of an executed act (caps.py EXECUTED_DECISION); no guard decision carries
// it.
const executedDecision = "executed"

// countsExecuted is caps.py counts_executed_acts: whether the fold's filter
// admits the engine's record of an executed act (spend.weekly/3.1.0).
func (f fold) countsExecuted() bool {
	for _, c := range f.filters {
		if c.field != "disposition.decision" {
			continue
		}
		if (c.op == "eq" && c.value == value{kind: str, s: executedDecision}) ||
			(c.op == "in" && slices.Contains(c.values, value{kind: str, s: executedDecision})) {
			return true
		}
	}
	return false
}

// unreadableExecuted is whether r is capsule-engine's record of an executed
// act whose spend it could not read (caps.py _unreadable_in_window): no
// amount, flagged spend_unreadable, which the fold skips.
func (r Record) unreadableExecuted() bool {
	return r.Decision != nil && *r.Decision == executedDecision && r.SpendUnreadable
}

// inWindowFor is whether r carries keyValue under the fold's key and falls
// in its window as of anchor: an act the window would count, by its key and
// time alone.
func (f fold) inWindowFor(r Record, keyValue string, anchor time.Time) bool {
	vals := map[string]value{"timestamp": recordValue(r, "timestamp")}
	k := recordValue(r, f.key)
	return k.kind == str && k.s == keyValue && f.within(vals, anchor)
}

// passes is engine.py _passes_filter for eq, ne and in.
func (f fold) passes(vals map[string]value) bool {
	for _, c := range f.filters {
		if c.op == "in" {
			if !slices.Contains(c.values, vals[c.field]) {
				return false
			}
			continue
		}
		if (vals[c.field] == c.value) != (c.op == "eq") {
			return false
		}
	}
	return true
}

// within is engine.py _within_window: [anchor - duration, anchor], inclusive.
func (f fold) within(vals map[string]value, anchor time.Time) bool {
	if f.window == 0 {
		return true
	}
	ts := vals["timestamp"]
	if ts.kind != str {
		return false
	}
	t, err := parseInstant(ts.s)
	return err == nil && !t.Before(anchor.Add(-f.window)) && !t.After(anchor)
}

// evaluate is engine.py evaluate_one for keyValue, as of anchor: the
// reducer over the records, in ledger order, that resolve, fall in the
// window, pass the filters and carry keyValue under the key. A group that
// never matched is the reducer's initial value, 0 (and an empty reversal
// summary).
//
// Under a reversal clause (engine.py _compute_groups and _reverse at
// c7fc3ac): a record whose class is a reversal class takes back its own
// amount, up to what the charge it cites has left, when it cites under the
// clause's relation a charge this fold already counted in the same group
// and its amount is positive; otherwise it is unlinked and takes back
// nothing. Every other record is a charge and counts max(amount, 0). Only
// this group's records are read: a charge in another group could never be
// taken back here (the engine refuses a charge whose group differs), so
// leaving them out gives the same result.
func (f fold) evaluate(records []Record, keyValue string, anchor time.Time) (int64, reversalSummary) {
	var acc int64
	var sum reversalSummary
	charges := map[string]int64{} // capsule id -> what the charge has left
	for _, r := range records {
		vals, ok := f.resolve(r)
		if !ok || !f.within(vals, anchor) || !f.passes(vals) {
			continue
		}
		if k := vals[f.key]; k.kind != str || k.s != keyValue {
			continue
		}
		if f.reducer == "count" {
			acc++
			continue
		}
		amount := vals[f.sumField].i
		rv := f.reversal
		if rv == nil {
			acc += amount
			continue
		}
		if class := vals[rv.ClassField]; class.kind == str && slices.Contains(rv.Classes, class.s) {
			taken := int64(0)
			parent, relation := vals[rv.ParentField], vals[rv.RelationField]
			if amount > 0 && relation.kind == str && relation.s == rv.Relation && parent.kind == str {
				if left, ok := charges[parent.s]; ok {
					taken = min(amount, left)
					charges[parent.s] = left - taken
				}
			}
			if taken > 0 {
				sum.linkedCount++
				sum.linkedMinor += taken
			} else {
				sum.unlinkedCount++
			}
			acc -= taken
			continue
		}
		counted := max(amount, 0)
		// engine.py _record_identity: the capsule id. A record without one
		// (a Go replay's own record) cannot be cited, so it is not registered.
		if r.CapsuleID != "" {
			charges[r.CapsuleID] = counted
		}
		acc += counted
	}
	return acc, sum
}
