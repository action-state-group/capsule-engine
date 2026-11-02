package admission

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// Served is what a card's inference-service-history/0 Standard answer counts
// (AAC spec/inference-service-history-profile-v0.md, cited as ISH;
// capsule-engine#273): the node's own count of the exchanges it served in
// each closed period, from the first period of its stream to the anchor.
// Every count is the node's statement, labelled self_reported (ISH §7.1),
// and the counts were never checked against records, so the coverage they
// declare is not established as complete (ISH §4.2).
type Served struct {
	// LengthS and Floor are the definition's period_policy length_s and
	// floor, which a verifier shows beside the counts (ISH §6.1 item 7).
	LengthS int64
	Floor   int64
	Periods []ServedPeriod
	// Verdicts are the closed periods' referee_verdicts cells for the
	// corroborated class (ISH §5.3), one per period, each with its value or
	// its suppression reason; a period whose answer carries no such cell has
	// neither. Nil when the answer counts no referee verdicts. The class is
	// the cell whose dimension is {"verdict": "corroborated"}: the profile
	// leaves how a class sits in a cell to vectors (SH-12, SH-14). The four
	// classes stay separate counts and are never added to one another or to
	// Periods (ISH §5.1, §5.3).
	Verdicts []ServedPeriod
	// VerdictsOnly is set when the answer counts referee verdicts and no
	// served exchanges: Periods is then empty and is not read.
	VerdictsOnly bool
}

// ServedPeriod is one closed period's exchanges_served total.
type ServedPeriod struct {
	From, To time.Time
	// Total is the published total; nil when it is suppressed.
	Total *int64
	// Suppressed is why Total is nil: SuppressedBelowFloor or
	// SuppressedComplementary (ISH §6.2). The evaluator reads any other
	// reason for a missing total as below the floor.
	Suppressed string
}

// Why a cell is suppressed (ISH §6.2).
const (
	SuppressedBelowFloor    = "below_floor"
	SuppressedComplementary = "complementary"
)

// CardCheckpoints is what the caller read from the answer's embedded
// history_card/1 when it verified the card as a direct history_card/1
// answer (ISH §5.2): its checkpoints in order, the stream's first to the
// anchor. The card's checkpoint form is the node's (a Mesh node's are
// COSE), so it is read by the caller's card verifier, never here.
type CardCheckpoints struct {
	Checkpoints []CardCheckpoint
}

// CardCheckpoint is one checkpoint of the card: its time, and the number of
// records of the stream it covers (a log size in records, not in tree
// nodes).
type CardCheckpoint struct {
	At      time.Time
	Records int64
}

// InferenceServiceHistoryError is why an answer is refused: the History
// property that does not hold, one of InferenceServiceHistoryProperties,
// and the detail. A caller records Property as Card.Reason; the detail can
// echo the counterparty's own strings, so it stays out of the decision.
type InferenceServiceHistoryError struct {
	Property, Detail string
}

func (e *InferenceServiceHistoryError) Error() string {
	return "inference-service-history/0 answer: " + e.Property + ": " + e.Detail
}

// InferenceServiceHistoryProperties are the properties an
// InferenceServiceHistoryError names (ISH §4.2, §6.3, §8.3, and
// anchor_match for the requester's pin).
var InferenceServiceHistoryProperties = []string{"definition_form", "definition_binding", "artifact_form",
	"anchor_match", "coverage_declaration_form", "coverage_declaration_anchor", "units", "labels",
	"period_alignment", "period_anchor", "period_floor"}

const (
	ishProfile     = "inference-service-history/0"
	ishCounts      = "period_counts"
	ishHistoryCard = "history_card"
	ishServed      = "exchanges_served"
	ishVerdicts    = "referee_verdicts"
	// ishVerdictDimension and ishCorroborated name the cell card.referee_verdicts
	// reads: the corroborated class of a verdict count (vector-only, SH-12).
	ishVerdictDimension = "verdict"
	ishCorroborated     = "corroborated"
	ishLabel            = "self_reported"
	// ishPeriodsMax is the most periods one answer may list: over 190
	// years of weeks, as the Mesh plugin bounds it.
	ishPeriodsMax = 10_000
)

// ishUnits is ISH §5.1's closed list of units.
var ishUnits = []string{"exchanges_served", "exchanges_requested", "exchanges_unattributed", "checkpoints",
	"witness_receipts", "referee_verdicts", "records"}

var (
	ishDefinitionKeys = []string{"coverage_declaration", "embeds", "excludes", "kind", "level", "members", "name",
		"period_policy", "profile", "reads", "spec", "subjects"}
	ishMemberKeys  = []string{"claims", "derivation", "name", "reads", "required", "units"}
	ishPolicyKeys  = []string{"epoch", "floor", "length_s", "min_length_s", "suppression"}
	ishNameRe      = regexp.MustCompile(`^[a-z0-9_]+/[0-9]+$`)
	ishWholeSecond = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`)
	ishHexDigest   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// InferenceServiceHistoryCounts reads a card's served periods from an
// inference-service-history/0 Standard answer: the definition document's
// bytes, the artifact's bytes, what the caller read from the embedded card,
// and the expected_pin the requester sent. Both documents must be JCS bytes,
// as StandardHistoryCounts requires of a card.
//
// What it reads comes only from the definition, resolved by its profile,
// and the artifact's counts: no record text. It checks every History
// property that is a function of those bytes and the card's checkpoints
// (ISH §4.2, §6.3, §8.3), and refuses the answer with the property named
// when one does not hold. A refusal means the card is CardUnverifiable. The
// envelope, the card's checkpoint signatures, consistency and witness
// receipts are the caller's (history_card_content), as for history_card/1.
//
// Only a Standard definition is read, and only counts in the period_counts
// shape of ISH §9 (unregistered, SH-12): the shape the Mesh plugin serves.
// Served holds the exchanges_served and referee_verdicts totals; nil when
// the answer counts neither. Exchanges in another unit are checked and never added
// to them (ISH §5.1). A refusal is an *InferenceServiceHistoryError.
func InferenceServiceHistoryCounts(definition, artifact []byte, card CardCheckpoints, pin string) (*Served, error) {
	return InferenceServiceHistoryCountsFor(definition, artifact, card, CoverageAsk{ExpectedPin: pin})
}

// CoverageAsk is the coverage the requester asked for (Evidence Request
// §3.2): exactly one of an expected_pin, a min_freshness of a log size, or a
// min_freshness of a time. The draft makes the forms mutually exclusive, so
// a responder cannot swap in fresher coverage for the pin that was demanded.
type CoverageAsk struct {
	// ExpectedPin is the anchor the requester pinned, or "".
	ExpectedPin string
	// MinSize is min_freshness by size: the fewest records the answer's
	// anchor must cover; nil when the ask is not by size.
	MinSize *int64
	// MinAt is min_freshness by time: the earliest time the answer's
	// anchor may have been issued; nil when the ask is not by time.
	MinAt *time.Time
}

// InferenceServiceHistoryCountsFor is InferenceServiceHistoryCounts for any
// coverage ask. A min_freshness the answer's anchor does not meet is refused
// as anchor_match, as a pin it does not equal is: the answer is a different
// answer to a different question, never a degraded success (§3.2). An ask
// that is none of the three forms, or more than one, is refused the same
// way. The anchor's size and time are the card's last checkpoint's, which
// the live_set below must match.
func InferenceServiceHistoryCountsFor(definition, artifact []byte, card CardCheckpoints, ask CoverageAsk) (*Served, error) {
	fail := func(property, format string, args ...any) error {
		return &InferenceServiceHistoryError{property, fmt.Sprintf(format, args...)}
	}
	if err := isJCS(definition); err != nil {
		return nil, fail("definition_form", "the definition is not JCS bytes: %v", err)
	}
	var def map[string]json.RawMessage
	if json.Unmarshal(definition, &def) != nil || def == nil {
		return nil, fail("definition_form", "the definition is not an object")
	}
	if profile, ok := ishString(def["profile"]); !ok || profile != ishProfile {
		return nil, fail("definition_form", "profile not known")
	}
	if level, _ := ishString(def["level"]); level == "detailed" {
		return nil, fail("definition_form", "detailed level not read")
	}
	d, derr := ishDefinitionOf(def)
	if derr != nil {
		return nil, fail(derr.property, "%s", derr.reason)
	}
	sum := sha256.Sum256(definition)
	digest := hex.EncodeToString(sum[:])

	if err := isJCS(artifact); err != nil {
		return nil, fail("artifact_form", "the artifact is not JCS bytes: %v", err)
	}
	var art map[string]json.RawMessage
	if json.Unmarshal(artifact, &art) != nil || art == nil {
		return nil, fail("artifact_form", "the artifact is not an object")
	}
	if derivation, _ := ishString(art["derivation"]); derivation != digest {
		return nil, fail("definition_binding", "the artifact's derivation is not the digest of the definition")
	}
	declared, ok := ishStrings(art["declared"])
	if !ok || !slices.IsSorted(declared) || len(slices.Compact(slices.Clone(declared))) != len(declared) {
		return nil, fail("artifact_form", "declared is not a sorted list of member names")
	}
	for _, m := range d.members {
		if m.required && !slices.Contains(declared, m.name) {
			return nil, fail("artifact_form", "the required member %s is not declared", m.name)
		}
	}
	// The artifact's own members (ISH §8.2), and then each declared one:
	// history_card among them, since it is required.
	keys := []string{"anchor", "coverage_declaration", "declared", "derivation", "evidence_stream",
		"history_card_digest", "subject"}
	for _, name := range declared {
		if !slices.ContainsFunc(d.members, func(m ishMember) bool { return m.name == name }) || slices.Contains(keys, name) {
			return nil, fail("artifact_form", "%s is declared and the definition does not list it", name)
		}
		keys = append(keys, name)
	}
	if !slices.Contains(declared, ishCounts) {
		return nil, fail("artifact_form", "the answer declares no counts member")
	}
	if !sameKeys(art, keys) {
		return nil, fail("artifact_form", "the artifact's members are not the ones it declares")
	}
	anchor, _ := ishString(art["anchor"])
	stream, _ := ishString(art["evidence_stream"])
	if !ishHexDigest.MatchString(anchor) || stream == "" || string(art["subject"]) != `{"full_history":null}` {
		return nil, fail("artifact_form", "anchor, evidence_stream or subject is not the profile's")
	}
	var hc map[string]json.RawMessage
	if json.Unmarshal(art["history_card"], &hc) != nil || hc == nil {
		return nil, fail("artifact_form", "history_card is not an object")
	}
	cardSum := sha256.Sum256(art["history_card"])
	if cd, _ := ishString(art["history_card_digest"]); cd != hex.EncodeToString(cardSum[:]) {
		return nil, fail("artifact_form", "the card does not match history_card_digest")
	}
	if cd, _ := ishString(hc["derivation"]); cd != "history_card/1" || string(hc["anchor"]) != string(art["anchor"]) ||
		string(hc["evidence_stream"]) != string(art["evidence_stream"]) || string(hc["subject"]) != string(art["subject"]) {
		return nil, fail("artifact_form", "the card is not for this answer's subject, stream and anchor")
	}
	forms := 0
	for _, set := range []bool{ask.ExpectedPin != "", ask.MinSize != nil, ask.MinAt != nil} {
		if set {
			forms++
		}
	}
	if forms != 1 || (ask.MinSize != nil && *ask.MinSize < 0) {
		return nil, fail("anchor_match", "the ask is not exactly one of expected_pin, a min_freshness size or a min_freshness time")
	}
	if ask.ExpectedPin != "" && anchor != ask.ExpectedPin {
		return nil, fail("anchor_match", "the answer's anchor is not the expected_pin requested")
	}
	if n := len(card.Checkpoints); n > 0 {
		last := card.Checkpoints[n-1]
		if ask.MinSize != nil && last.Records < *ask.MinSize {
			return nil, fail("anchor_match", "the answer's anchor covers fewer records than the min_freshness size requested")
		}
		if ask.MinAt != nil && last.At.Before(*ask.MinAt) {
			return nil, fail("anchor_match", "the answer's anchor was issued before the min_freshness time requested")
		}
	}

	// The coverage declaration (ISH §4.2): the definition's kind and rule,
	// and the members that depend on the anchor.
	var cov map[string]json.RawMessage
	if json.Unmarshal(art["coverage_declaration"], &cov) != nil || cov == nil ||
		!sameKeys(cov, []string{"interval", "kind", "live_set", "rule"}) ||
		string(cov["kind"]) != `"stream_interval"` || string(cov["rule"]) != `"all_records"` {
		return nil, fail("coverage_declaration_form", "not the Standard declaration")
	}
	var interval map[string]json.RawMessage
	if json.Unmarshal(cov["interval"], &interval) != nil || !sameKeys(interval, []string{"from", "to"}) ||
		string(interval["from"]) != "null" {
		return nil, fail("coverage_declaration_form", "interval is not from the stream's start")
	}
	if to, _ := ishString(interval["to"]); to != anchor {
		return nil, fail("coverage_declaration_anchor", "interval.to is not the anchor")
	}
	// The periods (ISH §6.3), against the card's checkpoints, which never go
	// back in time or in size.
	cps := card.Checkpoints
	if len(cps) == 0 {
		return nil, fail("period_alignment", "the card lists no checkpoint")
	}
	for i := 1; i < len(cps); i++ {
		if cps[i].At.Before(cps[i-1].At) || cps[i].Records < cps[i-1].Records {
			return nil, fail("period_alignment", "the card's checkpoints go back in time or in size")
		}
	}
	last := cps[len(cps)-1]
	liveSet, err := canon.JCS(canon.Arr([]canon.Value{canon.Obj(canon.M("checkpoint", canon.Str(anchor)),
		canon.M("count", canon.Int(last.Records)), canon.M("start", canon.Int(0)), canon.M("stream", canon.Str(stream)))}))
	if err != nil || string(cov["live_set"]) != liveSet {
		return nil, fail("coverage_declaration_form", "live_set is not the anchor's records in the answer's stream")
	}
	anchorAt := last.At
	if anchorAt.Nanosecond() != 0 || !d.onBoundary(anchorAt.Unix()) {
		return nil, fail("period_anchor", "the anchor's time is not a period boundary")
	}
	var counts map[string]json.RawMessage
	if json.Unmarshal(art[ishCounts], &counts) != nil || !sameKeys(counts, []string{"periods", "policy"}) {
		return nil, fail("artifact_form", "counts shape not known")
	}
	if policy, _ := ishString(counts["policy"]); policy != d.policyDigest {
		return nil, fail("period_alignment", "the counts name another policy")
	}
	var listed []json.RawMessage
	if json.Unmarshal(counts["periods"], &listed) != nil || listed == nil {
		return nil, fail("artifact_form", "counts shape not known")
	}
	// Every closed period, from the one the first checkpoint that commits a
	// record commits into (the one it ends, at a boundary) to the one the
	// anchor closes, each once, in order. Where no checkpoint commits a
	// record, no period is listed.
	end := d.periodOf(anchorAt.Unix())
	first := end
	if i := slices.IndexFunc(cps, func(c CardCheckpoint) bool { return c.Records > 0 }); i >= 0 {
		first = d.periodOf(cps[i].At.Unix())
		if d.onBoundary(cps[i].At.Unix()) && cps[i].At.Nanosecond() == 0 {
			first--
		}
	}
	if end-first > ishPeriodsMax {
		return nil, fail("period_alignment", "more than %d periods", ishPeriodsMax)
	}
	if int64(len(listed)) != end-first {
		return nil, fail("period_alignment", "the closed periods are not each listed once")
	}
	served := &Served{LengthS: d.lengthS, Floor: d.floor}
	hasServed := slices.Contains(d.counts.units, ishServed)
	if slices.Contains(d.counts.units, ishVerdicts) {
		served.Verdicts = []ServedPeriod{}
	}
	for i, raw := range listed {
		k := first + int64(i)
		var p map[string]json.RawMessage
		if json.Unmarshal(raw, &p) != nil || !sameKeys(p, []string{"cells", "from", "to"}) {
			return nil, fail("artifact_form", "counts shape not known")
		}
		from, okFrom := ishTime(p["from"])
		to, okTo := ishTime(p["to"])
		if !okFrom || !okTo || from.Unix() != d.startOf(k) || to.Unix() != d.startOf(k+1) {
			return nil, fail("period_alignment", "periods[%d] is not the policy's next closed period", i)
		}
		totals, corroborated, err := d.cells(p["cells"])
		if err != nil {
			return nil, fail(err.property, "periods[%d]: %s", i, err.reason)
		}
		if t, ok := totals[ishServed]; ok {
			served.Periods = append(served.Periods, ServedPeriod{From: from, To: to, Total: t.value, Suppressed: t.suppressed})
		}
		if _, ok := totals[ishVerdicts]; ok {
			c := ishTotal{}
			if corroborated != nil {
				c = *corroborated
			}
			served.Verdicts = append(served.Verdicts, ServedPeriod{From: from, To: to, Total: c.value, Suppressed: c.suppressed})
		}
	}
	if !hasServed && served.Verdicts == nil {
		return nil, nil
	}
	served.VerdictsOnly = !hasServed
	return served, nil
}

type ishMember struct {
	name     string
	required bool
	units    []string
}

type ishDefinition struct {
	members               []ishMember
	counts                ishMember
	epoch, lengthS, floor int64
	policyDigest          string
}

func (d ishDefinition) periodOf(t int64) int64 { return floorDiv(t-d.epoch, d.lengthS) }
func (d ishDefinition) startOf(k int64) int64  { return d.epoch + k*d.lengthS }
func (d ishDefinition) onBoundary(t int64) bool {
	return t-d.epoch-floorDiv(t-d.epoch, d.lengthS)*d.lengthS == 0
}

func floorDiv(a, b int64) int64 {
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

// ishDefinitionOf checks definition_form for a Standard definition (ISH
// §4.1, §8.3): exactly the members of §4.1 with the values it fixes, a
// required history_card member, members whose units are in §5.1, reads the
// union of the members' reads, and a period_policy that conforms (§6.1).
// The only counts member read is period_counts; any other is a shape not
// known.
func ishDefinitionOf(def map[string]json.RawMessage) (ishDefinition, *ishError) {
	var d ishDefinition
	formErr := func(format string, args ...any) *ishError {
		return &ishError{"definition_form", fmt.Sprintf(format, args...)}
	}
	if !sameKeys(def, ishDefinitionKeys) {
		return d, formErr("not exactly the members of a Standard definition")
	}
	name, _ := ishString(def["name"])
	_, spec := ishString(def["spec"])
	if string(def["level"]) != `"standard"` || string(def["kind"]) != `"projection"` ||
		string(def["embeds"]) != `["history_card/1"]` || string(def["subjects"]) != `["full_history"]` ||
		string(def["coverage_declaration"]) != `{"kind":"stream_interval","rule":"all_records"}` ||
		!ishNameRe.MatchString(name) || !spec {
		return d, formErr("a member does not have the value the profile fixes")
	}
	if excludes, ok := ishStrings(def["excludes"]); !ok || !slices.Contains(excludes, "requester identity") {
		return d, formErr("excludes does not name requester identity")
	}
	// Arrays are read one element at a time, as Rust reads them, so the
	// first bad element names the property in both.
	var members []json.RawMessage
	if json.Unmarshal(def["members"], &members) != nil || len(members) == 0 {
		return d, formErr("no members")
	}
	var union []string
	for i, raw := range members {
		var m map[string]json.RawMessage
		if json.Unmarshal(raw, &m) != nil || m == nil || !subsetKeys(m, ishMemberKeys) {
			return d, formErr("members[%d] is not a member", i)
		}
		var mb ishMember
		var ok bool
		if mb.name, ok = ishString(m["name"]); !ok || mb.name == "" ||
			slices.ContainsFunc(d.members, func(o ishMember) bool { return o.name == mb.name }) {
			return d, formErr("members[%d] has no name, or one listed twice", i)
		}
		switch string(m["required"]) {
		case "true", "false":
			mb.required = string(m["required"]) == "true"
		default:
			return d, formErr("%s has no required", mb.name)
		}
		reads, ok := ishStrings(m["reads"])
		if !ok {
			return d, formErr("%s has no reads", mb.name)
		}
		for _, r := range reads {
			if !slices.Contains(union, r) {
				union = append(union, r)
			}
		}
		var units []json.RawMessage
		if json.Unmarshal(m["units"], &units) != nil || len(units) == 0 {
			return d, formErr("%s has no units", mb.name)
		}
		for j, rawUnit := range units {
			var u map[string]json.RawMessage
			_ = json.Unmarshal(rawUnit, &u) // a non-object leaves u nil, refused below
			_, okWhere := ishString(u["where"])
			unit, okUnit := ishString(u["unit"])
			if !okWhere || !okUnit || !sameKeys(u, []string{"unit", "where"}) {
				return d, formErr("%s: units[%d] is not {where, unit}", mb.name, j)
			}
			if !slices.Contains(ishUnits, unit) {
				return d, &ishError{"units", fmt.Sprintf("%s counts in %q, which is not a unit of the profile", mb.name, unit)}
			}
			mb.units = append(mb.units, unit)
		}
		if raw, ok := m["derivation"]; ok {
			if _, isStr := ishString(raw); !isStr {
				return d, formErr("%s: derivation is not a string", mb.name)
			}
		}
		switch mb.name {
		case ishHistoryCard:
			if !mb.required || string(m["derivation"]) != `"history_card/1"` {
				return d, formErr("history_card is not a required history_card/1 member")
			}
		case ishCounts:
			d.counts = mb
		default:
			return d, formErr("counts shape not known: member %s", mb.name)
		}
		d.members = append(d.members, mb)
	}
	if !slices.ContainsFunc(d.members, func(m ishMember) bool { return m.name == ishHistoryCard }) {
		return d, formErr("history_card is not a required history_card/1 member")
	}
	if d.counts.name == "" {
		return d, formErr("no counts member")
	}
	if reads, ok := ishStrings(def["reads"]); !ok || !slices.Equal(reads, union) {
		return d, formErr("reads is not the union of the members' reads")
	}
	var p map[string]json.RawMessage
	if json.Unmarshal(def["period_policy"], &p) != nil || !sameKeys(p, ishPolicyKeys) ||
		string(p["suppression"]) != `"complementary"` {
		return d, formErr("period_policy is not the profile's")
	}
	epoch, ok := ishTime(p["epoch"])
	if !ok {
		return d, formErr("period_policy: epoch is not a whole-second UTC time")
	}
	var minLength int64
	for _, x := range []struct {
		k string
		v *int64
	}{{"length_s", &d.lengthS}, {"min_length_s", &minLength}, {"floor", &d.floor}} {
		n, err := cardInt(p[x.k])
		if err != nil || n <= 0 {
			return d, formErr("period_policy: %s is not a positive integer", x.k)
		}
		*x.v = n
	}
	if d.lengthS < minLength {
		return d, formErr("period_policy: length_s is under min_length_s")
	}
	d.epoch = epoch.Unix()
	sum := sha256.Sum256(def["period_policy"])
	d.policyDigest = hex.EncodeToString(sum[:])
	return d, nil
}

type ishTotal struct {
	value      *int64
	suppressed string
}

type ishError struct{ property, reason string }

// cells checks one period's cells (ISH §6.2, §7.3, §5.1): each cell is
// {unit, label, dimension, value} or, suppressed, with a reason and no
// value; its unit is one the counts member declares and its label
// self_reported; each unit has exactly one total (dimension null) and a
// dimension at most once; a published value is at least the floor; and
// complementary suppression holds. It returns each unit's total.
func (d ishDefinition) cells(raw json.RawMessage) (map[string]ishTotal, *ishTotal, *ishError) {
	bad := func(property, format string, args ...any) *ishError {
		return &ishError{property, fmt.Sprintf(format, args...)}
	}
	var cells []json.RawMessage
	if json.Unmarshal(raw, &cells) != nil || cells == nil {
		return nil, nil, bad("artifact_form", "counts shape not known")
	}
	type cell struct {
		dim string
		ishTotal
	}
	var corroborated *ishTotal
	byUnit := map[string][]cell{}
	for j, rawCell := range cells {
		var c map[string]json.RawMessage
		_ = json.Unmarshal(rawCell, &c) // a non-object leaves c nil, refused below
		unit, okUnit := ishString(c["unit"])
		if c == nil || !okUnit {
			return nil, nil, bad("artifact_form", "counts shape not known: cells[%d]", j)
		}
		if !slices.Contains(ishUnits, unit) || !slices.Contains(d.counts.units, unit) {
			return nil, nil, bad("units", "cells[%d] counts in %q, which its member does not declare", j, unit)
		}
		if label, _ := ishString(c["label"]); label != ishLabel {
			return nil, nil, bad("labels", "cells[%d] is labelled other than self_reported", j)
		}
		// A dimension is null on the unit's total, or one stated value.
		dim := ""
		class := ""
		if string(c["dimension"]) != "null" {
			var m map[string]json.RawMessage
			if json.Unmarshal(c["dimension"], &m) != nil || len(m) != 1 {
				return nil, nil, bad("artifact_form", "counts shape not known: cells[%d].dimension", j)
			}
			for k, v := range m {
				s, ok := ishString(v)
				if !ok {
					return nil, nil, bad("artifact_form", "counts shape not known: cells[%d].dimension", j)
				}
				if k == ishVerdictDimension {
					class = s
				}
			}
			dim = string(c["dimension"])
		}
		if slices.ContainsFunc(byUnit[unit], func(o cell) bool { return o.dim == dim }) {
			return nil, nil, bad("period_floor", "cells[%d] is listed twice", j)
		}
		var t ishTotal
		why, suppressed := ishString(c["suppressed"])
		switch {
		case suppressed && sameKeys(c, []string{"dimension", "label", "suppressed", "unit", "value"}) &&
			string(c["value"]) == "null" && (why == SuppressedBelowFloor || why == SuppressedComplementary):
			t.suppressed = why
		case !suppressed && sameKeys(c, []string{"dimension", "label", "unit", "value"}):
			n, err := cardCount(c["value"])
			if err != nil {
				return nil, nil, bad("period_floor", "cells[%d].value %v", j, err)
			}
			if n < d.floor {
				return nil, nil, bad("period_floor", "cells[%d] is published below the floor", j)
			}
			t.value = &n
		default:
			return nil, nil, bad("period_floor", "cells[%d] is neither published nor suppressed", j)
		}
		if unit == ishVerdicts && class == ishCorroborated {
			corroborated = &t
		}
		byUnit[unit] = append(byUnit[unit], cell{dim, t})
	}
	totals := map[string]ishTotal{}
	for _, unit := range d.counts.units {
		var total *ishTotal
		var dims []cell
		for i, c := range byUnit[unit] {
			if c.dim == "" {
				total = &byUnit[unit][i].ishTotal
			} else {
				dims = append(dims, c)
			}
		}
		if total == nil {
			return nil, nil, bad("artifact_form", "counts shape not known: no %s total", unit)
		}
		totals[unit] = *total
		if total.value == nil {
			continue
		}
		var withheld int
		var published int64
		for _, c := range dims {
			if c.value == nil {
				withheld++
			} else {
				// Each value is at most 2^53 − 1, so capping the sum just
				// past it keeps it inside int64 and still above any total.
				published = min(published+*c.value, maxSafeInteger+1)
			}
		}
		if withheld == 1 {
			return nil, nil, bad("period_floor", "a suppressed %s cell can be derived from the total", unit)
		}
		if published > *total.value || (withheld == 0 && len(dims) > 0 && published != *total.value) {
			return nil, nil, bad("period_floor", "the %s cells do not add up to the total", unit)
		}
	}
	return totals, corroborated, nil
}

// ishString reads a JSON string. encoding/json would read null as "", so
// null is refused here, as Rust refuses it.
func ishString(raw json.RawMessage) (string, bool) {
	var s string
	if raw == nil || string(raw) == "null" || json.Unmarshal(raw, &s) != nil {
		return "", false
	}
	return s, true
}

// ishStrings reads a JSON array of strings, null refused as a whole and as
// an element.
func ishStrings(raw json.RawMessage) ([]string, bool) {
	var items []json.RawMessage
	if raw == nil || string(raw) == "null" || json.Unmarshal(raw, &items) != nil {
		return nil, false
	}
	out := make([]string, 0, len(items))
	for _, it := range items {
		s, ok := ishString(it)
		if !ok {
			return nil, false
		}
		out = append(out, s)
	}
	return out, true
}

// ishTime reads a profile time: a whole second in UTC, written
// YYYY-MM-DDTHH:MM:SSZ, as the Mesh plugin writes period bounds.
func ishTime(raw json.RawMessage) (time.Time, bool) {
	s, ok := ishString(raw)
	if !ok || !ishWholeSecond.MatchString(s) {
		return time.Time{}, false
	}
	t, err := time.Parse(time.RFC3339, s)
	return t, err == nil
}

func sameKeys(m map[string]json.RawMessage, keys []string) bool {
	if len(m) != len(keys) {
		return false
	}
	for _, k := range keys {
		if _, ok := m[k]; !ok {
			return false
		}
	}
	return true
}

func subsetKeys(m map[string]json.RawMessage, keys []string) bool {
	for k := range m {
		if !slices.Contains(keys, k) {
			return false
		}
	}
	return true
}
