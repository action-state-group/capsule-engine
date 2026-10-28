package guard

import (
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// The statement checks asg/seller/0.1.0 cites, ported from capsule-engine
// ba76a97 (guards/checks/promise_never.py, promise_requires_approval.py,
// required_disclosure.py).
// They read the class of a
// statement, one member of a closed set, never the statement. A class
// outside the set is never shown: it is not one the rule knows, and could
// be anything.

// unknownClassNote is the failure of a statement whose class is not in the
// closed set.
const unknownClassNote = "the statement's representation_class is not one of the classes the rule knows"

// withArticle is cls after its indefinite article, as a reason names a
// class: "an authenticity", "a price" (promise_never.py with_article,
// capsule-engine #209).
func withArticle(cls string) string {
	if cls != "" && strings.ContainsRune("aeiou", rune(cls[0])) {
		return "an " + cls
	}
	return "a " + cls
}

// classRead is the class a statement check read, shown only when it is one
// the rule knows.
func classRead(cls string, recognised bool) *Read {
	if !recognised {
		return nil
	}
	return &Read{Field: "representation_class", Value: cls}
}

// checkPromiseNever: a class of statement the agent never makes
// (promise_never.py). A class in never fails whatever the action cites:
// authorized_by is recorded in the evidence, so a reader sees an approval
// was cited and did not count. A class outside the closed set fails closed.
// From 1.1.0 (attributed_never configured) the user's own claim, said as
// theirs and marked unverified, may pass (decideAttributed).
func checkPromiseNever(a Action, words statementWords, c neverConfig) (out Constraint) {
	const id, method = "promise_never", "class_membership_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "representation_class")
	if a.RepresentationClass == nil {
		return missingField(id, method, "representation_class")
	}
	rd = append(rd, "authorized_by")
	cls := *a.RepresentationClass
	recognised := slices.Contains(c.RepresentationClasses, cls)
	isNever := slices.Contains(c.Never, cls)
	base := []canon.Member{canon.M("representation_class", canon.Str(cls)), canon.M("recognised", canon.Bool(recognised)),
		canon.M("never", canon.Bool(isNever)), canon.M("authorized_by", strOrNull(a.AuthorizedBy))}
	evidence := canon.Obj(base...)
	if c.AttributedNever != nil {
		rd = append(rd, "statement_attributed_to")
		evidence = attributedEvidence(base, a, canon.Null(), canon.Null())
	}
	switch {
	case !recognised:
		out = decided(id, method, Fail, evidence)
		out.failNote = unknownClassNote
	case c.AttributedNever != nil:
		out = decideAttributed(a, words, c, cls, isNever, base, &rd)
	case isNever:
		out = decided(id, method, Fail, evidence)
		out.failNote = fmt.Sprintf("%s is a class of statement the agent never makes; no approval clears it, only a change to the rule set", cls)
	default:
		out = decided(id, method, Pass, evidence)
	}
	out.read = classRead(cls, recognised)
	return out
}

// attributedEvidence is promise_never/1.1.0's evidence: the class's, with
// whose claim the statement is, whether its words were opened, and whether
// they fit the template (null when not judged).
func attributedEvidence(base []canon.Member, a Action, opened, fits canon.Value) canon.Value {
	return canon.Obj(append(slices.Clone(base), canon.M("attributed_to", strOrNull(a.StatementAttributedTo)),
		canon.M("words_opened", opened), canon.M("fits_template", fits))...)
}

// wordsNotInBundle is why a replay cannot judge an attributed claim: the
// bundle carries no opening of its words, or the replayed record seals no
// commitment to them. It is never recorded as a failure, which would say the
// agent broke the rule over a claim the user made.
const wordsNotInBundle = "the words for this attributed claim are not in the bundle"

// decideAttributed is promise_never.py _decide_attributed, 1.1.0 on a
// recognised class: a class outside never passes; one inside it fails as
// the agent's own unless it is in attributed_never and attributed to the
// user, and then passes only when the words beside the check open the
// commitment the check seals and fit the template. On a replay whose
// bundle carries no words it is an in-scope n/a that fails closed.
func decideAttributed(a Action, words statementWords, c neverConfig, cls string, isNever bool, base []canon.Member, rd *[]string) Constraint {
	const id, method = "promise_never", "class_membership_v0"
	judged := func(r Result, opened, fits canon.Value, note string) Constraint {
		out := decided(id, method, r, attributedEvidence(base, a, opened, fits))
		out.failNote = note
		return out
	}
	notInBundle := func() Constraint {
		out := missingField(id, method, representingOpeningInput)
		// The rule applies and its evidence is not in the bundle: not
		// evaluated, never not applicable, and refused (fails closed).
		out.pending, out.failsClosed, out.unevaluated = wordsNotInBundle, true, true
		return out
	}
	if !isNever {
		return judged(Pass, canon.Null(), canon.Null(), "")
	}
	attributed := a.StatementAttributedTo != nil && *a.StatementAttributedTo == attributedToUser
	if !attributed || !slices.Contains(*c.AttributedNever, cls) {
		return judged(Fail, canon.Null(), canon.Null(),
			fmt.Sprintf("%s is a class of statement the agent never makes as its own; no approval clears it, only a change to the rule set", cls))
	}
	*rd = append(*rd, representingOpeningInput)
	sealed := a.representationTextCommitment
	switch {
	case sealed == nil && words.notInBundle:
		// A replayed record that seals the attribution but no commitment to
		// the words lacks the evidence, as it lacks words it cannot find.
		return notInBundle()
	case sealed == nil || !hex64.MatchString(*sealed):
		return judged(Fail, canon.Bool(false), canon.Null(), "the check seals no commitment to the words of this attributed claim")
	case words.notInBundle:
		return notInBundle()
	case isJSONNull(words.opening):
		return judged(Fail, canon.Null(), canon.Null(),
			fmt.Sprintf("a claim attributed to the user (%s) passes only with its words, and none were given with the check", cls))
	}
	opening := objectOf(words.opening)
	if !opensCommitment(opening["nonce"], opening["text"], *sealed) {
		return judged(Fail, canon.Bool(false), canon.Null(), "the words given with the check don't match the words sealed for this claim")
	}
	text, _ := stringOf(opening["text"])
	if t := *c.AttributedTemplate; !t.fits(text) {
		return judged(Fail, canon.Bool(true), canon.Bool(false), fmt.Sprintf(
			"your claim must read \"%s… %s\", one sentence, so the buyer sees whose claim it is and that it is unverified",
			t.Prefix, strings.TrimFunc(t.Suffix, pythonSpace)))
	}
	return judged(Pass, canon.Bool(true), canon.Bool(true), "")
}

// approvalClassPath and authorizedPath are where promise_requires_approval
// reads, one member per level: the class a sealed approval record approves
// (CLASS_PATH), and the classes a sealed task-authority record allows
// (AUTHORIZED_PATH).
var (
	approvalClassPath = []string{"body", "representation_class"}
	authorizedPath    = []string{"body", "authorized_representation_classes"}
)

// at walks raw down path, one object member per level: the value there, or
// false when some level is not an object or lacks the member.
func at(raw json.RawMessage, path []string) (json.RawMessage, bool) {
	for _, name := range path {
		var obj map[string]json.RawMessage
		if json.Unmarshal(raw, &obj) != nil || obj == nil {
			return nil, false
		}
		v, ok := obj[name]
		if !ok {
			return nil, false
		}
		raw = v
	}
	return raw, true
}

// boundTo is whether this port's own digest of record (SHA-256 over its JCS
// form) is ref: false when there is no record, an error when it has no
// digest.
func boundTo(record json.RawMessage, ref string) (bool, error) {
	if isJSONNull(record) {
		return false, nil
	}
	v, err := canon.FromJSON(record)
	if err != nil {
		return false, err
	}
	d, err := canon.Digest(v)
	if err != nil {
		return false, err
	}
	return d == ref, nil
}

// authorityAllows is _authority_allows: the bound task-authority record
// lists cls at path. A record the reference does not bind, or one that
// lists nothing there, allows nothing. With known (1.1.0), the list counts
// only when every member is one of those class names: a list holding
// anything else is malformed and allows nothing.
func authorityAllows(a Action, record json.RawMessage, cls string, path, known []string) bool {
	if a.TaskAuthorityRef == nil {
		return false
	}
	if bound, err := boundTo(record, *a.TaskAuthorityRef); err != nil || !bound {
		return false
	}
	raw, ok := at(record, path)
	if !ok {
		return false
	}
	var listed []json.RawMessage
	if json.Unmarshal(raw, &listed) != nil || listed == nil {
		return false
	}
	found := false
	for _, item := range listed {
		s, isString := stringOf(item)
		if known != nil && (!isString || !slices.Contains(known, s)) {
			return false
		}
		found = found || (isString && s == cls)
	}
	return found
}

// NoApprovalRecord is why promise_requires_approval does not evaluate a
// statement outside the task authority that cites an approval, on an engine
// given no approval records (Engine.WithoutApprovalRecords): the approval it
// reads is a sealed record naming the class, and no record capsulectl seals
// carries one, so the plugin cannot read it. Never a pass.
const NoApprovalRecord = "the statement cites an approval (authorized_by) that this plugin cannot read: no sealed record it is given carries an approval for a class of statement"

// errUnapproved is _Unapproved: the supplied approval does not approve the
// statement. The message says why, naming no digest and no class the
// record names.
var errUnapproved = errors.New("unapproved")

func unapproved(why string) error { return fmt.Errorf("%w: %s", errUnapproved, why) }

// approvedClass is _approved_class: the class the bound approval names.
func approvedClass(a Action, record json.RawMessage) (string, error) {
	if a.AuthorizedBy == nil {
		return "", unapproved("the action cites no approval (authorized_by)")
	}
	if isJSONNull(record) {
		return "", unapproved("no approval record was supplied for authorized_by")
	}
	bound, err := boundTo(record, *a.AuthorizedBy)
	switch {
	case err != nil:
		return "", unapproved("the supplied approval record has no digest")
	case !bound:
		return "", unapproved("ref mismatch: the supplied approval record is not the record authorized_by names")
	}
	raw, ok := at(record, approvalClassPath)
	if !ok {
		return "", unapproved("the approval record names no class at body.representation_class")
	}
	var cls string
	if json.Unmarshal(raw, &cls) != nil {
		return "", unapproved("the approval record's body.representation_class is not one class")
	}
	return cls, nil
}

// checkPromiseRequiresApproval: a class of statement the agent may make
// only inside its task authority or with a one-shot approval
// (promise_requires_approval.py). A class outside requires_approval passes.
// One inside it passes when the task-authority record task_authority_ref
// binds lists it, or when the approval record authorized_by binds names it;
// the evidence's authority_basis says which. Anything less fails, never
// n/a: an unreadable approval is no approval. A class outside the closed
// set fails.
//
// The one place this port departs from the engine: when noRecords is set
// (Engine.WithoutApprovalRecords), an action outside its task authority
// whose authorized_by cites an approval the caller can never supply is n/a,
// not evaluated (NoApprovalRecord), which the report reads as
// not_evaluable: never a pass, never an allow. An action that cites no
// approval still fails, as the engine fails it.
func checkPromiseRequiresApproval(a Action, taskAuthority, authorization json.RawMessage, c approvalConfig, noRecords bool) (out Constraint) {
	const id, method = "promise_requires_approval", "authority_or_bound_approval_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "representation_class")
	if a.RepresentationClass == nil {
		return missingField(id, method, "representation_class")
	}
	rd = append(rd, "task_authority_ref", "authorized_by")
	cls := *a.RepresentationClass
	recognised := slices.Contains(c.RepresentationClasses, cls)
	needs := slices.Contains(c.RequiresApproval, cls)
	result := func(r Result, basis string) Constraint {
		b := canon.Null()
		if basis != "" {
			b = canon.Str(basis)
		}
		out := decided(id, method, r, canon.Obj(canon.M("representation_class", canon.Str(cls)),
			canon.M("recognised", canon.Bool(recognised)), canon.M("requires_approval", canon.Bool(needs)),
			canon.M("authority_basis", b), canon.M("task_authority_ref", strOrNull(a.TaskAuthorityRef)),
			canon.M("authorized_by", strOrNull(a.AuthorizedBy))))
		out.read = classRead(cls, recognised)
		return out
	}
	switch {
	case !recognised:
		out = result(Fail, "")
		out.failNote = unknownClassNote
		return out
	case !needs:
		return result(Pass, "")
	case authorityAllows(a, taskAuthority, cls, c.authorizedPath(), c.knownOrdered()):
		return result(Pass, "task_authority_ref")
	}
	if noRecords && a.AuthorizedBy != nil && isJSONNull(authorization) {
		out = notEvaluable(id, method, NoApprovalRecord)
		out.read = classRead(cls, recognised)
		return out
	}
	approved, err := approvedClass(a, authorization)
	if err == nil && approved == cls {
		return result(Pass, "authorized_by")
	}
	why := "the approval is for another class"
	if err != nil {
		why = strings.TrimPrefix(err.Error(), errUnapproved.Error()+": ")
	}
	out = result(Fail, "")
	out.failNote = fmt.Sprintf("%s statement is outside the task authority and needs a bound approval: %s", withArticle(cls), why)
	if c.AuthorizedPath != nil && a.TaskAuthorityRef == nil {
		// 1.1.0: what the user's card says (capsule-engine #194 §2); the
		// evidence still records the class and what was cited.
		out.failNote = "no order or approval of yours covers this promise"
	}
	return out
}

// requiredConfig is required_disclosure's config: the closed set of
// statement classes, the classes that must have been stated first, the
// action classes it applies to, and the fold that counts them; from 1.1.0,
// also the fold that counts the statements in the action's deal.
type requiredConfig struct {
	ActionClasses         []string `yaml:"action_classes"`
	RepresentationClasses []string `yaml:"representation_classes"`
	RequiredClasses       []string `yaml:"required_classes"`
	FoldID                string   `yaml:"fold_id"`
	FoldDigest            string   `yaml:"fold_digest"`
	StatementFoldID       string   `yaml:"statement_fold_id"`
	StatementFoldDigest   string   `yaml:"statement_fold_digest"`
	// CountListingClasses (required_disclosure/1.2.0, capsule-engine #204)
	// also counts a class the sale's listings in force state
	// (Action.ListingClasses). It builds on 1.1.0's statement fold.
	CountListingClasses bool `yaml:"count_listing_classes"`
}

// errListingWithoutStatements is required_disclosure.py's refusal of
// count_listing_classes with no statement fold: 1.2.0 builds on 1.1.0.
var errListingWithoutStatements = errors.New("count_listing_classes goes with statement_fold_id: required_disclosure/1.2.0 builds on 1.1.0")

type requiredDisclosure struct {
	fold            fold
	actionClasses   []string
	requiredClasses []string
	// statements is required_disclosure/1.1.0's statement fold
	// (disclosure.statement_seen/1.0.0), or nil under 1.0.0.
	statements *fold
	// countListing is required_disclosure/1.2.0's count_listing_classes.
	countListing bool
}

// newRequiredDisclosure resolves the wicket's fold by id and digest from
// the fold catalog, as the engine does (seen_before_fold), and holds it to
// the per-class count the check reads.
func newRequiredDisclosure(w pack.Wicket, p pack.Pack) (requiredDisclosure, error) {
	cfg, err := decodeConfig[requiredConfig](w)
	if err != nil {
		return requiredDisclosure{}, err
	}
	if err := requireKnownClasses(cfg.RequiredClasses, cfg.RepresentationClasses, w.ID+" required_classes"); err != nil {
		return requiredDisclosure{}, err
	}
	f, ok := p.CatalogFold(cfg.FoldID)
	if !ok || f.Digest != cfg.FoldDigest {
		return requiredDisclosure{}, fmt.Errorf("%s cites fold %s at %s, which the fold catalog does not resolve to that digest", w.ID, cfg.FoldID, cfg.FoldDigest)
	}
	fl, err := newFold(f)
	if err != nil {
		return requiredDisclosure{}, err
	}
	if fl.reducer != "count" || fl.key != "asg_payload.representation_class" || fl.window != 0 {
		return requiredDisclosure{}, fmt.Errorf("%s: fold %s is not the per-class count the check reads", w.ID, f.ID)
	}
	rd := requiredDisclosure{fold: fl, actionClasses: cfg.ActionClasses, requiredClasses: cfg.RequiredClasses,
		countListing: cfg.CountListingClasses}
	// checks/__init__.py: the statement fold only when the config names one.
	if cfg.StatementFoldID == "" {
		if cfg.CountListingClasses {
			// required_disclosure.py raises on this config.
			return requiredDisclosure{}, fmt.Errorf("%s: %w", w.ID, errListingWithoutStatements)
		}
		return rd, nil
	}
	sf, ok := p.CatalogFold(cfg.StatementFoldID)
	if !ok || sf.Digest != cfg.StatementFoldDigest {
		return requiredDisclosure{}, fmt.Errorf("%s cites fold %s at %s, which the fold catalog does not resolve to that digest", w.ID, cfg.StatementFoldID, cfg.StatementFoldDigest)
	}
	sfl, err := newFold(sf)
	if err != nil {
		return requiredDisclosure{}, err
	}
	if sfl.reducer != "count" || sfl.key != "asg_payload.stated.class" || sfl.window != 0 {
		return requiredDisclosure{}, fmt.Errorf("%s: fold %s is not the per-class statement count the check reads", w.ID, sf.ID)
	}
	rd.statements = &sfl
	return rd, nil
}

// checkRequiredDisclosure: has each required class of statement been made
// to this counterparty before the action (required_disclosure.py)? For each
// required class, the fold's count over this operator's records addressed
// to the action's target (accepted, not dry runs); a zero fails, and the
// evidence lists every count and the classes still missing. It never reads
// an approval: one does not waive a statement the user required.
func checkRequiredDisclosure(a Action, h *historyReader, c requiredDisclosure) (out Constraint) {
	const id = "required_disclosure"
	method := c.fold.id
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.actionClasses, a.class()) {
		return outOfScope(id, method)
	}
	if c.statements != nil {
		rd = append(rd, "target", "deal_id", "operator")
		if c.countListing {
			rd = append(rd, "listing_classes")
		}
		return checkRequiredStatements(a, h, c)
	}
	rd = append(rd, "target", "operator")
	if a.Target == nil {
		return missingField(id, method, "target")
	}
	counts, told := classCounts(c.fold, toTarget(h, a), c.requiredClasses)
	missing := []string{}
	for _, cls := range c.requiredClasses {
		if told[cls] == 0 {
			missing = append(missing, cls)
		}
	}
	evidence := canon.Obj(canon.M("fold", canon.Str(c.fold.digest)),
		canon.M("scope", canon.Obj(canon.M("operator", canon.Str(a.Operator)), canon.M("target", canon.Str(*a.Target)))),
		canon.M("prior_counts", counts), canon.M("missing_classes", canon.Strs(missing)))
	return disclosureDecided(id, method, missing, evidence)
}

// toTarget is required_disclosure.py _to_target: a's operator's records
// addressed to its target (ScanQuery(counterparty=action.operator), then the
// target), or none when a names no target.
func toTarget(h *historyReader, a Action) []Record {
	if a.Target == nil {
		return nil
	}
	var mine []Record
	for _, r := range h.find(Query{Where: map[string]string{FieldOperator: a.Operator, FieldTarget: *a.Target}}) {
		if r.Operator == a.Operator && r.Target != nil && *r.Target == *a.Target {
			mine = append(mine, r)
		}
	}
	return mine
}

// classCounts is f's count of records per class, as an evidence object and
// by class (required_disclosure.py _counted, _counts).
func classCounts(f fold, records []Record, classes []string) (canon.Value, map[string]int64) {
	members := make([]canon.Member, 0, len(classes))
	by := make(map[string]int64, len(classes))
	for _, cls := range classes {
		n, _ := f.evaluate(records, cls, time.Time{})
		members = append(members, canon.M(cls, canon.Int(n)))
		by[cls] = n
	}
	return canon.Obj(members...), by
}

// checkRequiredStatements is required_disclosure/1.1.0
// (required_disclosure.py _check_with_statements, capsule-engine #139): a
// required class counts as stated to the counterparty when an accepted
// earlier action of a's operator to a's target carried it (as 1.0.0), or
// when a's deal already holds a statement of it (statementsIn: a claim the
// operator's agent made in the deal before a, StatementOf). An action naming
// neither a target nor a deal is n/a, the target missing; one with only a
// target counts the counterparty's statements, one with only a deal the
// deal's. Both counts are in the evidence. The deal stands for the
// counterparty here (capsulectl opens one thread per buyer); a statement
// names no counterparty, so a counterparty that changes within a deal is
// not seen here (counterparty_identity_change reads that).
//
// Under required_disclosure/1.2.0 (countListing; required_disclosure.py at
// d336c3a) a class also counts as stated when it is in a's listing_classes,
// the classes every live listing of the sale stated as its check sealed
// them; with none, or an empty list, a listing counts for nothing. The
// evidence then also lists that value (null with none).
func checkRequiredStatements(a Action, h *historyReader, c requiredDisclosure) Constraint {
	const id = "required_disclosure"
	method := c.fold.id
	if a.Target == nil && a.DealID == nil {
		return missingField(id, method, "target")
	}
	var inDeal []Record
	if a.DealID != nil {
		inDeal = statementsIn(h, a.Operator, *a.DealID)
	}
	prior, told := classCounts(c.fold, toTarget(h, a), c.requiredClasses)
	stated, said := classCounts(*c.statements, inDeal, c.requiredClasses)
	missing := []string{}
	for _, cls := range c.requiredClasses {
		if told[cls] == 0 && said[cls] == 0 && !(c.countListing && slices.Contains(a.ListingClasses, cls)) {
			missing = append(missing, cls)
		}
	}
	members := []canon.Member{canon.M("fold", canon.Str(c.fold.digest)), canon.M("statement_fold", canon.Str(c.statements.digest)),
		canon.M("scope", canon.Obj(canon.M("operator", canon.Str(a.Operator)), canon.M("target", strOrNull(a.Target)),
			canon.M("deal_id", strOrNull(a.DealID)))),
		canon.M("prior_counts", prior), canon.M("stated_counts", stated), canon.M("missing_classes", canon.Strs(missing))}
	if c.countListing {
		listed := canon.Null()
		if a.ListingClasses != nil {
			listed = canon.Strs(a.ListingClasses)
		}
		members = append(members, canon.M("listing_classes", listed))
	}
	evidence := canon.Obj(members...)
	return disclosureDecided(id, method, missing, evidence)
}

// disclosureDecided is required_disclosure.py _decided: a pass when no
// required class is missing, else a fail naming the missing classes.
func disclosureDecided(id, method string, missing []string, evidence canon.Value) Constraint {
	if len(missing) == 0 {
		return decided(id, method, Pass, evidence)
	}
	out := decided(id, method, Fail, evidence)
	out.failNote = fmt.Sprintf("no accepted prior statement of %s to this counterparty: make the disclosure first or change the task authority; an approval does not waive it",
		strings.Join(missing, ", "))
	return out
}
