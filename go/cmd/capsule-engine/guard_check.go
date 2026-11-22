package main

import (
	"bytes"
	"embed"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"

	"github.com/action-state-group/capsule-engine/go/guard"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// guard check: one external-check-input/v0 on stdin, one
// external-check-result/v0 on stdout. It is a deal profile's rules checker
// (capsulectl `profile update --rules-checker`) for the embedded packs: the
// record and its history are checked with the Go guard and each rule becomes
// one finding. The two schemas are evidencebook's public copies
// (dealprofile/x-deal-v0), vendored byte for byte in schemas/ with
// schemas/PROVENANCE.json. The input is validated before it is read and the
// result before it is printed, so a result that does not validate is never
// printed.
//
// Exit status as for the other commands: 0 when a result was printed,
// whatever its verdict; 2 otherwise, with nothing on stdout and one line on
// stderr that names no value from the input.
//
// The checker is one self-contained binary (capsulectl copies the
// executable alone): the packs and the schemas are embedded, nothing is read
// from beside it. It applies the pack's own limits; a caller that lets a
// person set limits of their own does that outside this command.

//go:embed schemas/external-check-input-v0.schema.json schemas/external-check-result-v0.schema.json
var checkSchemas embed.FS

// --pack names an embedded pack id (for example asg/everyday/0.3.8); without
// it the pack follows the input's rules_pin, else its party_role (buyer or
// none asg/everyday/0.3.8, seller asg/seller/0.1.5).
const guardCheckUsage = "usage: capsule-engine guard check [--pack ID] < external-check-input.json"

const externalResultSchema = "external-check-result/v0"

// externalResult is external-check-result/v0. tier is always recomputed:
// every check here is a deterministic recomputation from the record and its
// history, with no model and no person in the loop.
type externalResult struct {
	Schema           string            `json:"schema"`
	RulesetID        string            `json:"ruleset_id"`
	DefinitionDigest string            `json:"definition_digest"`
	Verdict          string            `json:"verdict"`
	Tier             string            `json:"tier"`
	Findings         []externalFinding `json:"findings"`
}

// externalFinding is one finding. Limit and Value are numbers (minor units)
// or absent: a sentence is for a person, and a figure in a string can read as
// personal data. Reason is a short clause, on fail and not_evaluable only.
type externalFinding struct {
	ID      string `json:"id"`
	Check   string `json:"check,omitempty"`
	Verdict string `json:"verdict"`
	Reason  string `json:"reason,omitempty"`
	Limit   *int64 `json:"limit,omitempty"`
	Value   *int64 `json:"value,omitempty"`
}

func guardCheck(args []string, stdin io.Reader, stdout io.Writer) error {
	fs := flag.NewFlagSet("guard check", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	packID := fs.String("pack", "", "an embedded pack id")
	if err := fs.Parse(args); err != nil || fs.NArg() > 0 {
		return errors.New(guardCheckUsage)
	}
	raw, err := io.ReadAll(stdin)
	if err != nil {
		return fmt.Errorf("read stdin: %w", err)
	}
	if err := validateAgainst("external-check-input-v0.schema.json", raw); err != nil {
		return err
	}
	pin, err := rulesPinOf(raw)
	if err != nil {
		return err
	}
	a, h, err := guard.DecodeCheckInput(raw)
	if err != nil {
		return err
	}
	p, err := packFor(*packID, a, pin)
	if err != nil {
		return err
	}
	e, err := guard.NewEngine(p)
	if err != nil {
		return err
	}
	// No path here is given an approval record: capsulectl seals none for a
	// class of statement (guard.NoApprovalRecord).
	e.WithoutApprovalRecords()
	var rep guard.CheckReport
	if len(a.Unevaluable()) > 0 {
		rep = e.ReportUnevaluable(a)
	} else {
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			return err
		}
		rep = e.Report(a, d)
	}
	out, err := json.Marshal(externalResultOf(rep))
	if err != nil {
		return err
	}
	if err := validateAgainst("external-check-result-v0.schema.json", out); err != nil {
		return fmt.Errorf("the result this checker built does not validate (a defect, not an input fault): %w", err)
	}
	_, err = fmt.Fprintln(stdout, string(out))
	return err
}

// validateAgainst validates raw against an embedded schema. A failure names
// where in the instance it failed and which keyword, never a value: the input
// carries values a refusal must not repeat on stderr (a payee keyed per
// profile, a sale's item reference, the opening of a floor).
func validateAgainst(name string, raw []byte) error {
	src, err := checkSchemas.ReadFile("schemas/" + name)
	if err != nil {
		return err
	}
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(src))
	if err != nil {
		return fmt.Errorf("parse schema %s: %w", name, err)
	}
	c := jsonschema.NewCompiler()
	if err := c.AddResource(name, doc); err != nil {
		return err
	}
	sch, err := c.Compile(name)
	if err != nil {
		return err
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		return fmt.Errorf("input is not JSON: %w", err)
	}
	if err := sch.Validate(inst); err != nil {
		var ve *jsonschema.ValidationError
		if !errors.As(err, &ve) {
			return fmt.Errorf("%s: does not validate", name)
		}
		return fmt.Errorf("%s: does not validate: %s", name, strings.Join(failedAt(ve), "; "))
	}
	return nil
}

// failedAt is each innermost failure of ve as its instance location and the
// schema keyword that failed, sorted, with no value.
func failedAt(ve *jsonschema.ValidationError) []string {
	if len(ve.Causes) == 0 {
		return []string{fmt.Sprintf("at '/%s': %s", strings.Join(ve.InstanceLocation, "/"), strings.Join(ve.ErrorKind.KeywordPath(), "/"))}
	}
	var out []string
	for _, c := range ve.Causes {
		out = append(out, failedAt(c)...)
	}
	slices.Sort(out)
	return slices.Compact(out)
}

// rulesPin is the input's rules_pin: on a profile that pins rules per role,
// the acting role's pin alone. It names the pack (id) or the definition
// digests the person pinned; the checker runs no other pack.
type rulesPin struct {
	Pack              string   `json:"pack"`
	DefinitionDigests []string `json:"definition_digests"`
}

// rulesPinOf reads the validated envelope's rules_pin (nil when absent).
func rulesPinOf(raw []byte) (*rulesPin, error) {
	var env struct {
		RulesPin *rulesPin `json:"rules_pin"`
	}
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("rules_pin is not {pack, definition_digests}")
	}
	if p := env.RulesPin; p != nil && p.Pack == "" && len(p.DefinitionDigests) == 0 {
		return nil, fmt.Errorf("rules_pin names neither a pack nor a definition digest")
	}
	return env.RulesPin, nil
}

// partyPacks is the pack a deal is checked against, by party_role.
var partyPacks = map[string]func() (pack.Pack, error){
	"":               pack.LoadEveryday038,
	pack.PartyBuyer:  pack.LoadEveryday038,
	pack.PartySeller: pack.LoadSeller015,
}

// packFor chooses the pack, in this order: --pack; the acting role's
// rules_pin; the embedded pack for the deal's party_role. The module chooses
// no default itself; this command's defaults are partyPacks. A party_role
// with no pack left the action unevaluable when it was decoded (no pack runs
// for it); the not_evaluable result then names the buyer pack's identity,
// because a result must name one.
func packFor(id string, a guard.Action, pin *rulesPin) (pack.Pack, error) {
	if id != "" {
		return embeddedPack(func(p pack.Pack) bool { return p.ID == id }, "no embedded pack has that id")
	}
	role := ""
	if a.PartyRole != nil {
		role = *a.PartyRole
	}
	if pin != nil {
		// A pin that names digests and no pack keeps the acting role's own
		// pack when its digest is among them, so a flat set naming both packs
		// runs the acting side's, not whichever is listed first.
		if pin.Pack == "" && pack.KnownPartyRole(role) {
			if p, err := partyPacks[role](); err == nil && slices.Contains(pin.DefinitionDigests, p.DefinitionDigest) {
				return p, nil
			}
		}
		return embeddedPack(func(p pack.Pack) bool {
			return (pin.Pack == "" || p.ID == pin.Pack) &&
				(len(pin.DefinitionDigests) == 0 || slices.Contains(pin.DefinitionDigests, p.DefinitionDigest))
		}, "the deal profile pins rules this checker does not carry: re-pin the role to an embedded pack")
	}
	if !pack.KnownPartyRole(role) {
		return pack.LoadEveryday038()
	}
	return partyPacks[role]()
}

// embeddedPack is the first embedded pack that match accepts. A pin naming a
// pack not carried is refused, never answered under another pack.
func embeddedPack(match func(pack.Pack) bool, none string) (pack.Pack, error) {
	for _, e := range pack.Embedded() {
		p, err := e.Load()
		if err != nil {
			return pack.Pack{}, err
		}
		if match(p) {
			return p, nil
		}
	}
	return pack.Pack{}, errors.New(none)
}

// externalResultOf maps the consolidated report onto the result: the pack's
// id and digest, the decision, and every rule (the engine's reference check
// included) in pack order. A rule with limits of its own (caps: the
// per-purchase limit and the rolling-window limit) is one finding per limit,
// its id the rule id, "#" and the limit (r05-spending-limits#per_purchase,
// r05-spending-limits#7d), each with its verdict, limit and value; the rule's
// own verdict still decides the result's.
func externalResultOf(rep guard.CheckReport) externalResult {
	r := externalResult{Schema: externalResultSchema, RulesetID: rep.Pack.ID, DefinitionDigest: rep.Pack.DefinitionDigest, Verdict: rep.Decision, Tier: "recomputed"}
	if rep.Refused {
		// The guard refused although no rule failed (for example a checked
		// record whose profile cannot be read): the result is a deny, which
		// offers no way to go ahead.
		r.Verdict = guard.Deny
	}
	for _, rule := range rep.Rules {
		if len(rule.Parts) > 0 {
			r.Findings = append(r.Findings, capsFindings(rule)...)
			continue
		}
		f := externalFinding{ID: rule.Rule, Check: rule.Check, Verdict: rule.Verdict}
		if rule.Verdict == guard.VerdictFail || rule.Verdict == guard.VerdictNotEvaluable || statesNo(rule.Reason) {
			f.Reason = shortReason(rule.Reason)
		}
		r.Findings = append(r.Findings, f)
	}
	return r
}

// capsFindings is one finding per caps limit: the per-purchase limit against
// the amount it read (the authorised maximum, or the capture), and the window
// limit against the projected total, which sums captures. A value not
// computed is left out.
func capsFindings(rule guard.RuleResult) []externalFinding {
	window := "7d"
	if rule.Window != nil && rule.Window.Duration != "" {
		window = rule.Window.Duration
	}
	var out []externalFinding
	for _, p := range rule.Parts {
		f := externalFinding{Check: rule.Check, Verdict: p.Verdict}
		threshold := p.ThresholdMinor
		f.Limit = &threshold
		if p.Verdict != guard.VerdictNotEvaluable {
			observed := p.ObservedMinor
			f.Value = &observed
		}
		authorised := false
		if b := basisOf(rule); b != nil && !b.FellBack {
			authorised = true
		}
		// A limit the person set says so in the reason; the pack's own keeps
		// the words it had.
		whose := ""
		if p.LimitSource == guard.LimitSourceProfile {
			whose = " you set"
		}
		switch {
		case p.Limit == "per_action":
			f.ID = rule.Rule + "#per_purchase"
			if p.Verdict == guard.VerdictFail && authorised {
				f.Reason = "the authorised maximum is over the per-purchase limit" + whose
			} else if p.Verdict == guard.VerdictFail {
				f.Reason = "the amount is over the per-purchase limit" + whose
			}
		default:
			f.ID = rule.Rule + "#" + window
			if p.Verdict == guard.VerdictFail {
				f.Reason = "the " + windowWords(window) + " total with this purchase is over the " + windowWords(window) + " limit" + whose
			}
		}
		if p.Verdict == guard.VerdictNotEvaluable {
			f.Reason = shortReason(p.Reason)
		}
		out = append(out, f)
	}
	return out
}

func basisOf(rule guard.RuleResult) *guard.RuleBasis {
	if rule.Value == nil {
		return nil
	}
	return rule.Value.PerActionBasis
}

// windowWords is a window duration as a person reads it: "7d" is "7-day".
func windowWords(d string) string {
	if n, ok := strings.CutSuffix(d, "d"); ok && n != "" {
		return n + "-day"
	}
	return d
}

// shortReasons map the engine's reasons (which name fields, versions and
// totals) to one clause a person reads on the deal card, with no figure in
// it. The first match wins.
var shortReasons = []struct{ contains, short string }{
	{"the history supplied is incomplete", "the history of earlier purchases supplied is incomplete"},
	{"the history supplied reaches back", "the history of earlier purchases supplied is shorter than the window"},
	{"carry no recorded decision", "an earlier act has no recorded decision, so the total cannot count it"},
	{"classed under another taxonomy version", "an earlier purchase was classed under another taxonomy version"},
	{"has an action_class and no taxonomy_version", "the record has an action class and no taxonomy version"},
	{"declares no action_class", "the action declares no action class"},
	{"ref mismatch: the supplied task-authority record", "the deal states no task_authority_record matching its task_authority_ref"},
	{"is not in the action-class table", "the action's class is not in the action-class table"},
	{"the offer's expiry could not be established", "the age of the proposal could not be established"},
	{"deal_id_conflict", "deal_id_conflict: the record names two different deals"},
	{"no commercial-bounds opening was supplied", "the floor in force could not be read"},
	{"does not open the bounds_commitment", "the floor's opening does not match the floor the task authority seals"},
	{"the bounds_commitment the task authority seals is not", "the floor the task authority seals is malformed"},
	{"below the committed floor", "the amount is below the floor"},
}

// statesNo is whether a not_applicable rule's reason names a field the deal
// does not state: the rule applies, the engine decided without it, and the
// deal card says which field.
func statesNo(reason string) bool {
	return strings.HasPrefix(reason, "the deal states no ") || strings.HasPrefix(reason, "the action states no ")
}

// recordTaxonomy matches the engine's reason for a record sealed at another
// action taxonomy: the record's version, then the engine's.
var recordTaxonomy = regexp.MustCompile(`^taxonomy_version (\S+) on the action; the engine's action taxonomy is version (\S+):`)

// taxonomyReason is the card's words for a check sealed at another action
// taxonomy, which is refused: the tool that recorded it is older than the
// rules (an earlier version number), or one they do not know.
func taxonomyReason(reason string) (string, bool) {
	m := recordTaxonomy.FindStringSubmatch(reason)
	if m == nil {
		return "", false
	}
	record, errRecord := strconv.Atoi(m[1])
	engine, errEngine := strconv.Atoi(m[2])
	if errRecord == nil && errEngine == nil && record < engine {
		return "this check was recorded by an older capsulectl; update capsulectl to check it", true
	}
	return "this check was recorded by a capsulectl your rules do not know; update capsulectl and your rules", true
}

// shortReason is reason as one short clause: a known reason's short form,
// else its first clause (before "; " or ": "). An action_class_gate reason
// that begins "Asks: " is already the ruled sentence the user reads
// (capsule-engine#182), so it is kept whole.
func shortReason(reason string) string {
	if short, ok := taxonomyReason(reason); ok {
		return short
	}
	if strings.HasPrefix(reason, "Asks: ") {
		return reason
	}
	for _, r := range shortReasons {
		if strings.Contains(reason, r.contains) {
			return r.short
		}
	}
	for _, sep := range []string{"; ", ": "} {
		if head, _, ok := strings.Cut(reason, sep); ok {
			reason = head
		}
	}
	return reason
}
