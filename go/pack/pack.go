// Package pack reads an Action State pack definition and computes its
// definition digest the way capsule-engine does
// (capsule_engine/packs/schema.py PackDefinition.definition_digest at
// 559288d0f61a): SHA-256 over the JCS bytes of the canonical pack projection.
// Python stays the reference implementation; a test pins this port to the
// engine-computed golden recorded beside each pack. Any pack key this port
// does not canonicalise is refused rather than skipped, because skipping it
// would produce a digest the engine would not.
package pack

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path"
	"slices"
	"strings"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/canon"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// ForwardVerdict is python/capsule_engine/compiler/vocabulary.py FORWARD_VERDICTS.
// There is no model-valued forward verdict: a rule that needs a model is
// UNAVAILABLE-MODEL-REQUIRED.
type ForwardVerdict string

const (
	ForwardDeterministic    ForwardVerdict = "DETERMINISTIC"
	ForwardUnavailableModel ForwardVerdict = "UNAVAILABLE-MODEL-REQUIRED"
	ForwardUnavailableState ForwardVerdict = "UNAVAILABLE-STATE-REQUIRED"
	ForwardRefused          ForwardVerdict = "REFUSED"
)

var (
	forwardVerdicts     = []string{"DETERMINISTIC", "UNAVAILABLE-MODEL-REQUIRED", "UNAVAILABLE-STATE-REQUIRED", "REFUSED"}
	backwardVerdicts    = []string{"DETERMINISTIC", "MODEL-ASSISTED", "MANUAL", "WITH-INSTRUMENTATION", "REFUSED"}
	defaultDispositions = []string{"DO", "ASK", "NEVER"}
)

// Implementation is the label a rendered rule carries.
type Implementation string

const (
	Enforced Implementation = "ENFORCED"
	Asked    Implementation = "ASKED"
	Unknown  Implementation = "UNKNOWN"
)

// Rule is one rule of a pack, in pack order: obligations, then outcomes.
type Rule struct {
	ID string
	// Check is the deterministic check an obligation maps to ("" for an
	// outcome).
	Check              string
	Statement          string
	DefaultDisposition string
	ForwardVerdict     ForwardVerdict
	// Selector is the one selector of Check this rule is measured by (""
	// when Check has no selectors): the rule fails or passes only when that
	// selector matched, and is not applicable when it did not.
	Selector string
	// Declared: the obligation is declared_not_measured (it cites no check
	// and names the evidence instrument it would need).
	Declared bool
}

// Implementation derives the label from the forward verdict alone: ENFORCED
// only for DETERMINISTIC, ASKED for UNAVAILABLE-MODEL-REQUIRED, UNKNOWN for
// anything else. A rule that needs a model can never be ENFORCED.
func (r Rule) Implementation() Implementation {
	switch r.ForwardVerdict {
	case ForwardDeterministic:
		return Enforced
	case ForwardUnavailableModel:
		return Asked
	case ForwardUnavailableState, ForwardRefused:
		return Unknown
	}
	return Unknown
}

// Pack is a loaded pack: its id, its engine-equivalent definition digest, and
// its rules.
type Pack struct {
	ID               string
	DefinitionDigest string
	Rules            []Rule
	// Wickets are the cited wicket definitions, in pack constraint order,
	// each digest-verified against the pack's citation.
	Wickets []Wicket
	// Folds are the cited fold definitions, each digest-verified against the
	// pack's citation.
	Folds []Fold
	// catalog is every fold definition in the catalog the pack was loaded
	// against, cited or not, by id, each with its computed digest.
	catalog map[string]Fold
	// Taxonomy is the action taxonomy the pack pins, nil when it pins none.
	// A pinned pack loads only when the pin names the vendored taxonomy.
	Taxonomy *TaxonomyPin
}

// TaxonomyPin is a pack's taxonomy pin (capsule-engine packs/schema.py
// TaxonomyPin, 47c58cd): the taxonomy_version and the SHA-256 over the
// taxonomy's JCS bytes. It is inside the pack's definition digest.
type TaxonomyPin struct {
	Version, Digest string
}

// TaxonomyPinError is a pack pinning an action taxonomy other than the
// vendored one (the engine's taxonomy_pin_mismatch).
type TaxonomyPinError struct {
	PackID, Version, Digest, VendoredVersion, VendoredDigest string
}

func (e *TaxonomyPinError) Error() string {
	return fmt.Sprintf("pack %s: taxonomy pins version %q digest %q; this plugin vendors version %q digest %q",
		e.PackID, e.Version, e.Digest, e.VendoredVersion, e.VendoredDigest)
}

// Wicket is one cited wicket definition. Config is the definition's own
// config, for the check that implements it to decode.
type Wicket struct {
	ID, Check, Digest string
	Config            yaml.Node
}

// Wicket returns the cited wicket configuring check.
func (p Pack) Wicket(check string) (Wicket, bool) {
	i := slices.IndexFunc(p.Wickets, func(w Wicket) bool { return w.Check == check })
	if i < 0 {
		return Wicket{}, false
	}
	return p.Wickets[i], true
}

// Fold returns the cited fold with the given id.
func (p Pack) Fold(id string) (Fold, bool) {
	i := slices.IndexFunc(p.Folds, func(f Fold) bool { return f.ID == id })
	if i < 0 {
		return Fold{}, false
	}
	return p.Folds[i], true
}

// CatalogFold returns the fold with the given id from the catalog the pack
// was loaded against, whether or not the pack cites it: the engine reads a
// wicket's fold from its core fold catalog by id and digest
// (guards/checks seen_before_fold), not from the pack's folds.
func (p Pack) CatalogFold(id string) (Fold, bool) {
	f, ok := p.catalog[id]
	return f, ok
}

// Rule returns the rule with the given id.
func (p Pack) Rule(id string) (Rule, bool) {
	i := slices.IndexFunc(p.Rules, func(r Rule) bool { return r.ID == id })
	if i < 0 {
		return Rule{}, false
	}
	return p.Rules[i], true
}

type rawObligation struct {
	ID        string `yaml:"id"`
	Statement string `yaml:"statement"`
	Check     string `yaml:"check"`
	// Selector (capsule-engine packs/loader.py _obligation_selector at
	// a5e9e30): required on, and only on, an obligation whose check's wicket
	// config has selectors.
	Selector            string `yaml:"selector"`
	ReDerivabilityGrade string `yaml:"re_derivability_grade"`
	DefaultDisposition  string `yaml:"default_disposition"`
	// Measurability and EvidenceInstrument (capsule-engine packs/loader.py
	// and schema.py Obligation at #88's head): an obligation is measured by
	// its check unless it declares declared_not_measured, when it cites no
	// check and names the instrument its missing input would arrive in.
	Measurability      string         `yaml:"measurability"`
	EvidenceInstrument *rawInstrument `yaml:"evidence_instrument"`
}

// rawInstrument is schema.py EvidenceInstrument: kind structured_field with a
// field, or kind tool_call_name with a name.
type rawInstrument struct {
	Kind  string `yaml:"kind"`
	Field string `yaml:"field"`
	Name  string `yaml:"name"`
}

func (in rawInstrument) canonical(what string) (canon.Value, error) {
	switch {
	case in.Kind == "structured_field" && in.Field != "" && in.Name == "":
		return canon.Obj(canon.M("kind", canon.Str(in.Kind)), canon.M("field", canon.Str(in.Field))), nil
	case in.Kind == "tool_call_name" && in.Name != "" && in.Field == "":
		return canon.Obj(canon.M("kind", canon.Str(in.Kind)), canon.M("name", canon.Str(in.Name))), nil
	}
	return canon.Value{}, fmt.Errorf("%s.evidence_instrument must be kind structured_field with a field, or kind tool_call_name with a name", what)
}

type rawActionSemantic struct {
	ActionType     string            `yaml:"action_type"`
	ActionClass    string            `yaml:"action_class"`
	RequiredFields []string          `yaml:"required_fields"`
	OptionalFields []string          `yaml:"optional_fields"`
	FieldAliases   map[string]string `yaml:"field_aliases"`
}

type rawConstraint struct {
	WicketRef string   `yaml:"wicket_ref"`
	Digest    string   `yaml:"digest"`
	Scope     []string `yaml:"scope"`
}

type rawFold struct {
	FoldRef string `yaml:"fold_ref"`
	Digest  string `yaml:"digest"`
}

type rawClause struct {
	Instrument   string  `yaml:"instrument"`
	Article      string  `yaml:"article"`
	Paragraph    *string `yaml:"paragraph"`
	Jurisdiction *string `yaml:"jurisdiction"`
}

type rawOutcome struct {
	ID              string     `yaml:"id"`
	Statement       string     `yaml:"statement"`
	EvidenceRule    string     `yaml:"evidence_rule"`
	ForwardVerdict  string     `yaml:"forward_verdict"`
	BackwardVerdict string     `yaml:"backward_verdict"`
	Mode            string     `yaml:"mode"`
	Profile         string     `yaml:"profile"`
	Tier            string     `yaml:"tier"`
	Clause          *rawClause `yaml:"clause"`
	// DefaultDisposition is read for the render. At capsule-engine
	// 559288d0f61a the engine does not parse it on an outcome, so it is NOT
	// part of the canonical projection or the digest; this port mirrors that
	// (TestOutcomeDispositionNotDigestCovered pins it).
	DefaultDisposition string `yaml:"default_disposition"`
}

type rawPack struct {
	PackID           string              `yaml:"pack_id"`
	Obligations      []rawObligation     `yaml:"obligations"`
	ActionSemantics  []rawActionSemantic `yaml:"action_semantics"`
	Constraints      []rawConstraint     `yaml:"constraints"`
	Folds            []rawFold           `yaml:"folds"`
	HoldsIntegration *string             `yaml:"holds_integration"`
	Outcomes         []rawOutcome        `yaml:"outcomes"`
	Taxonomy         *rawTaxonomy        `yaml:"taxonomy"`
	// Not part of the canonical projection.
	Fixtures  yaml.Node `yaml:"fixtures"`
	Bootstrap string    `yaml:"bootstrap"`
}

type rawTaxonomy struct {
	Version string `yaml:"taxonomy_version"`
	Digest  string `yaml:"digest"`
}

type rawWicket struct {
	WicketID string    `yaml:"wicket_id"`
	Check    string    `yaml:"check"`
	Config   yaml.Node `yaml:"config"`
	// Semantics is the definition's normative statement (capsule-engine
	// guards/wickets/definition.py, 47c58cd): in the digested body when set.
	Semantics *string `yaml:"semantics"`
}

type wicket struct {
	id, check, digest string
	config            yaml.Node
}

func decodeStrict(data []byte, out *rawPack) error {
	dec := yaml.NewDecoder(bytes.NewReader(data))
	dec.KnownFields(true)
	if err := dec.Decode(out); err != nil {
		return fmt.Errorf("pack.yaml (a key this port does not canonicalise is refused): %w", err)
	}
	return nil
}

// loadWickets reads every *.yaml wicket definition in dir of fsys, computing
// each one's digest as the engine does: JSON-DIGEST of {wicket_id, check,
// config}, plus semantics when the definition states it.
func loadWickets(fsys fs.FS, dir string) (map[string]wicket, error) {
	entries, err := fs.ReadDir(fsys, dir)
	if err != nil {
		return nil, fmt.Errorf("wicket catalog: %w", err)
	}
	out := make(map[string]wicket, len(entries))
	for _, e := range entries {
		if e.IsDir() || path.Ext(e.Name()) != ".yaml" {
			continue
		}
		data, err := fs.ReadFile(fsys, path.Join(dir, e.Name()))
		if err != nil {
			return nil, err
		}
		var w rawWicket
		dec := yaml.NewDecoder(bytes.NewReader(data))
		dec.KnownFields(true)
		if err := dec.Decode(&w); err != nil {
			return nil, fmt.Errorf("wicket %s: %w", e.Name(), err)
		}
		cfg, err := canon.FromYAML(&w.Config)
		if err != nil {
			return nil, fmt.Errorf("wicket %s config: %w", e.Name(), err)
		}
		members := []canon.Member{
			canon.M("wicket_id", canon.Str(w.WicketID)),
			canon.M("check", canon.Str(w.Check)),
			canon.M("config", cfg),
		}
		if w.Semantics != nil {
			if strings.TrimSpace(*w.Semantics) == "" {
				return nil, fmt.Errorf("wicket %s: semantics must be a non-empty string", e.Name())
			}
			members = append(members, canon.M("semantics", canon.Str(*w.Semantics)))
		}
		d, err := canon.Digest(canon.Obj(members...))
		if err != nil {
			return nil, fmt.Errorf("wicket %s: %w", e.Name(), err)
		}
		if _, dup := out[w.WicketID]; dup {
			return nil, fmt.Errorf("wicket_id %q defined twice", w.WicketID)
		}
		out[w.WicketID] = wicket{id: w.WicketID, check: w.Check, digest: d, config: w.Config}
	}
	return out, nil
}

func nonEmpty(what, v string) error {
	if strings.TrimSpace(v) == "" {
		return fmt.Errorf("%s is required", what)
	}
	return nil
}

func oneOf(what, v string, allowed []string) error {
	if !slices.Contains(allowed, v) {
		return fmt.Errorf("%s=%q must be one of %v", what, v, allowed)
	}
	return nil
}

// Load reads pack.yaml bytes, resolves each constraint's wicket_ref and each
// fold_ref against the definitions in catalogDir/wickets and catalogDir/folds
// of catalogFS (refusing a cited digest the definition does not produce, as
// the engine loader does), and computes the definition digest.
func Load(packYAML []byte, catalogFS fs.FS, catalogDir string) (Pack, error) {
	var raw rawPack
	if err := decodeStrict(packYAML, &raw); err != nil {
		return Pack{}, err
	}
	if err := nonEmpty("pack_id", raw.PackID); err != nil {
		return Pack{}, err
	}
	wickets, err := loadWickets(catalogFS, path.Join(catalogDir, "wickets"))
	if err != nil {
		return Pack{}, err
	}
	foldDefs, err := loadFolds(catalogFS, path.Join(catalogDir, "folds"))
	if err != nil {
		return Pack{}, err
	}

	constraints := make([]canon.Value, 0, len(raw.Constraints))
	var cited []Wicket
	declaredChecks := map[string]bool{}
	// checkSelectors are the selector ids of each cited check whose wicket
	// config has selectors (loader.py check_selectors).
	checkSelectors := map[string][]string{}
	for i, c := range raw.Constraints {
		if c.WicketRef == "" {
			return Pack{}, fmt.Errorf("constraints[%d]: only wicket_ref citations are supported by this port", i)
		}
		w, ok := wickets[c.WicketRef]
		if !ok {
			return Pack{}, fmt.Errorf("constraints[%d]: wicket_ref %q is not in the vendored catalog", i, c.WicketRef)
		}
		if w.digest != c.Digest {
			return Pack{}, &CitationDigestError{PackID: raw.PackID, Field: fmt.Sprintf("constraints[%d]", i), Ref: c.WicketRef, Cited: c.Digest, Computed: w.digest}
		}
		if w.check == "caps" && c.Scope == nil {
			return Pack{}, fmt.Errorf("constraints[%d]: a caps constraint must declare scope", i)
		}
		declaredChecks[w.check] = true
		if ids, ok := selectorIDs(w.config); ok {
			checkSelectors[w.check] = ids
		}
		cited = append(cited, Wicket{ID: w.id, Check: w.check, Digest: w.digest, Config: w.config})
		members := []canon.Member{canon.M("wicket_id", canon.Str(w.id)), canon.M("check", canon.Str(w.check)), canon.M("digest", canon.Str(w.digest))}
		if c.Scope != nil {
			members = append(members, canon.M("scope", canon.Strs(c.Scope)))
		}
		constraints = append(constraints, canon.Obj(members...))
	}

	var rules []Rule
	seen := map[string]bool{}
	addRule := func(r Rule) error {
		if seen[r.ID] {
			return fmt.Errorf("rule id %q declared more than once", r.ID)
		}
		seen[r.ID] = true
		rules = append(rules, r)
		return nil
	}

	obligations := make([]canon.Value, 0, len(raw.Obligations))
	for i, o := range raw.Obligations {
		for _, err := range []error{
			nonEmpty(fmt.Sprintf("obligations[%d].id", i), o.ID),
			nonEmpty(fmt.Sprintf("obligations[%q].statement", o.ID), o.Statement),
			oneOf(fmt.Sprintf("obligations[%q].default_disposition", o.ID), o.DefaultDisposition, defaultDispositions),
		} {
			if err != nil {
				return Pack{}, err
			}
		}
		what := fmt.Sprintf("obligations[%q]", o.ID)
		// schema.py PackDefinition canonical projection: check,
		// measurability and evidence_instrument are emitted only when set
		// (measurability only when not "measured").
		members := []canon.Member{canon.M("id", canon.Str(o.ID)), canon.M("statement", canon.Str(o.Statement))}
		rule := Rule{ID: o.ID, Statement: o.Statement, DefaultDisposition: o.DefaultDisposition}
		switch o.Measurability {
		case "", "measured":
			if o.EvidenceInstrument != nil {
				return Pack{}, fmt.Errorf("%s is measured by its check and also names an evidence_instrument", what)
			}
			if !declaredChecks[o.Check] {
				return Pack{}, fmt.Errorf("obligations[%q].check=%q has no matching constraint", o.ID, o.Check)
			}
			members = append(members, canon.M("check", canon.Str(o.Check)))
			if err := checkSelector(what, o, checkSelectors); err != nil {
				return Pack{}, err
			}
			if o.Selector != "" {
				members = append(members, canon.M("selector", canon.Str(o.Selector)))
			}
			rule.Selector = o.Selector
			// An obligation's check is a declared wicket constraint (the
			// engine loader refuses one that is not), so its forward verdict
			// is DETERMINISTIC by construction.
			rule.Check, rule.ForwardVerdict = o.Check, ForwardDeterministic
		case "declared_not_measured":
			if o.Check != "" {
				return Pack{}, fmt.Errorf("%s declares measurability=declared_not_measured and also cites check=%q", what, o.Check)
			}
			if o.Selector != "" {
				return Pack{}, fmt.Errorf("%s names a selector but cites no check; a selector belongs only to an obligation measured by a check that has selectors", what)
			}
			if o.EvidenceInstrument == nil {
				return Pack{}, fmt.Errorf("%s declares measurability=declared_not_measured but no evidence_instrument", what)
			}
			// No check evaluates it: the input it needs is state no action
			// records yet, so its forward verdict is UNAVAILABLE-STATE-
			// REQUIRED and its label UNKNOWN, never ENFORCED.
			rule.ForwardVerdict, rule.Declared = ForwardUnavailableState, true
		default:
			return Pack{}, fmt.Errorf("%s.measurability=%q must be measured or declared_not_measured", what, o.Measurability)
		}
		if o.ReDerivabilityGrade != "" {
			members = append(members, canon.M("re_derivability_grade", canon.Str(o.ReDerivabilityGrade)))
		}
		members = append(members, canon.M("default_disposition", canon.Str(o.DefaultDisposition)))
		if o.Measurability == "declared_not_measured" {
			instrument, err := o.EvidenceInstrument.canonical(what)
			if err != nil {
				return Pack{}, err
			}
			members = append(members, canon.M("measurability", canon.Str(o.Measurability)), canon.M("evidence_instrument", instrument))
		}
		obligations = append(obligations, canon.Obj(members...))
		if err := addRule(rule); err != nil {
			return Pack{}, err
		}
	}

	semantics := make([]canon.Value, 0, len(raw.ActionSemantics))
	for _, a := range raw.ActionSemantics {
		members := []canon.Member{
			canon.M("action_type", canon.Str(a.ActionType)),
			canon.M("action_class", canon.Str(a.ActionClass)),
			canon.M("required_fields", canon.Strs(a.RequiredFields)),
		}
		if len(a.OptionalFields) > 0 {
			members = append(members, canon.M("optional_fields", canon.Strs(a.OptionalFields)))
		}
		if len(a.FieldAliases) > 0 {
			var aliases []canon.Member
			for k, v := range a.FieldAliases {
				aliases = append(aliases, canon.M(k, canon.Str(v)))
			}
			members = append(members, canon.M("field_aliases", canon.Obj(aliases...)))
		}
		semantics = append(semantics, canon.Obj(members...))
	}

	folds := make([]canon.Value, 0, len(raw.Folds))
	var citedFolds []Fold
	for i, f := range raw.Folds {
		if f.FoldRef == "" || f.Digest == "" {
			return Pack{}, fmt.Errorf("folds[%d]: only fold_ref citations with a digest are supported by this port", i)
		}
		def, ok := foldDefs[f.FoldRef]
		if !ok {
			return Pack{}, fmt.Errorf("folds[%d]: fold_ref %q is not in the vendored catalog", i, f.FoldRef)
		}
		if def.Digest != f.Digest {
			return Pack{}, &CitationDigestError{PackID: raw.PackID, Field: fmt.Sprintf("folds[%d]", i), Ref: f.FoldRef, Cited: f.Digest, Computed: def.Digest}
		}
		citedFolds = append(citedFolds, def)
		folds = append(folds, canon.Obj(canon.M("fold_id", canon.Str(def.ID)), canon.M("digest", canon.Str(def.Digest))))
	}

	outcomes := make([]canon.Value, 0, len(raw.Outcomes))
	for i, o := range raw.Outcomes {
		v, err := canonicalOutcome(i, o)
		if err != nil {
			return Pack{}, err
		}
		outcomes = append(outcomes, v)
		if err := addRule(Rule{ID: o.ID, Statement: o.Statement, DefaultDisposition: o.DefaultDisposition, ForwardVerdict: ForwardVerdict(o.ForwardVerdict)}); err != nil {
			return Pack{}, err
		}
	}

	holds := "none"
	if raw.HoldsIntegration != nil {
		holds = *raw.HoldsIntegration
	}
	top := []canon.Member{
		canon.M("pack_id", canon.Str(raw.PackID)),
		canon.M("obligations", canon.Arr(obligations)),
		canon.M("action_semantics", canon.Arr(semantics)),
		canon.M("constraints", canon.Arr(constraints)),
		canon.M("folds", canon.Arr(folds)),
		canon.M("holds_integration", canon.Str(holds)),
	}
	if len(outcomes) > 0 {
		top = append(top, canon.M("outcomes", canon.Arr(outcomes)))
	}
	var pin *TaxonomyPin
	if raw.Taxonomy != nil {
		if pin, err = checkTaxonomyPin(raw.PackID, *raw.Taxonomy); err != nil {
			return Pack{}, err
		}
		top = append(top, canon.M("taxonomy", canon.Obj(
			canon.M("taxonomy_version", canon.Str(pin.Version)),
			canon.M("digest", canon.Str(pin.Digest)),
		)))
	}
	digest, err := canon.Digest(canon.Obj(top...))
	if err != nil {
		return Pack{}, err
	}
	if len(rules) == 0 {
		return Pack{}, errors.New("pack declares no rules")
	}
	return Pack{ID: raw.PackID, DefinitionDigest: digest, Rules: rules, Wickets: cited, Folds: citedFolds, catalog: foldDefs, Taxonomy: pin}, nil
}

// checkTaxonomyPin is capsule-engine packs/loader.py _parse_taxonomy_pin
// (47c58cd): both fields are required, and the pin must name the taxonomy
// this plugin vendors (packs/engine/action_taxonomy.json), by version and
// by the SHA-256 over its JCS bytes.
func checkTaxonomyPin(packID string, raw rawTaxonomy) (*TaxonomyPin, error) {
	if err := nonEmpty("taxonomy.taxonomy_version", raw.Version); err != nil {
		return nil, err
	}
	if err := nonEmpty("taxonomy.digest", raw.Digest); err != nil {
		return nil, err
	}
	version, digest, err := VendoredTaxonomy()
	if err != nil {
		return nil, err
	}
	if raw.Version != version || raw.Digest != digest {
		return nil, &TaxonomyPinError{PackID: packID, Version: raw.Version, Digest: raw.Digest, VendoredVersion: version, VendoredDigest: digest}
	}
	return &TaxonomyPin{Version: raw.Version, Digest: raw.Digest}, nil
}

// VendoredTaxonomy is the vendored action taxonomy's taxonomy_version and
// the SHA-256 over its JCS bytes, what a pack's taxonomy pin must name.
func VendoredTaxonomy() (version, digest string, err error) {
	var doc struct {
		Version string `json:"taxonomy_version"`
	}
	if err := json.Unmarshal(packs.ActionTaxonomy, &doc); err != nil {
		return "", "", fmt.Errorf("vendored action taxonomy: %w", err)
	}
	v, err := canon.FromJSON(packs.ActionTaxonomy)
	if err != nil {
		return "", "", fmt.Errorf("vendored action taxonomy: %w", err)
	}
	d, err := canon.Digest(v)
	if err != nil {
		return "", "", fmt.Errorf("vendored action taxonomy: %w", err)
	}
	return doc.Version, d, nil
}

// selectorIDs are the ids of a wicket config's selectors, sorted, when the
// config has a selectors key.
func selectorIDs(config yaml.Node) ([]string, bool) {
	if config.Kind != yaml.MappingNode {
		return nil, false
	}
	for i := 0; i+1 < len(config.Content); i += 2 {
		if config.Content[i].Value != "selectors" {
			continue
		}
		var ids []string
		if sel := config.Content[i+1]; sel.Kind == yaml.MappingNode {
			for j := 0; j < len(sel.Content); j += 2 {
				ids = append(ids, sel.Content[j].Value)
			}
		}
		slices.Sort(ids)
		return ids, true
	}
	return nil, false
}

// checkSelector is loader.py _obligation_selector: an obligation citing a
// check with selectors names exactly one of them, and one citing a check
// without selectors names none. A check with selectors produces one result
// for the whole action, so an obligation with no selector would fail
// whenever any selector fails.
func checkSelector(what string, o rawObligation, checkSelectors map[string][]string) error {
	ids, has := checkSelectors[o.Check]
	e := &SelectorError{What: what, Check: o.Check, Selector: o.Selector, Selectors: ids}
	switch {
	case !has && o.Selector != "":
		e.Kind = SelectorOnCheckWithout
	case has && o.Selector == "":
		e.Kind = SelectorMissing
	case has && !slices.Contains(ids, o.Selector):
		e.Kind = SelectorNotOfCheck
	default:
		return nil
	}
	return e
}

// SelectorKind is which selector rule an obligation breaks.
type SelectorKind int

const (
	// SelectorOnCheckWithout: a selector on a check that has none.
	SelectorOnCheckWithout SelectorKind = iota + 1
	// SelectorMissing: no selector on a check that has selectors.
	SelectorMissing
	// SelectorNotOfCheck: a selector the check does not name.
	SelectorNotOfCheck
)

// SelectorError is an obligation's selector refused by checkSelector.
type SelectorError struct {
	Kind                  SelectorKind
	What, Check, Selector string
	Selectors             []string
}

func (e *SelectorError) Error() string {
	switch e.Kind {
	case SelectorOnCheckWithout:
		return fmt.Sprintf("%s.selector=%q, but check %q has no selectors; drop the selector", e.What, e.Selector, e.Check)
	case SelectorMissing:
		return fmt.Sprintf("%s cites check %q, which has selectors %v; name the one this obligation is measured by", e.What, e.Check, e.Selectors)
	}
	return fmt.Sprintf("%s.selector=%q is not a selector of check %q (selectors: %v)", e.What, e.Selector, e.Check, e.Selectors)
}

func canonicalOutcome(i int, o rawOutcome) (canon.Value, error) {
	for _, err := range []error{
		nonEmpty(fmt.Sprintf("outcomes[%d].id", i), o.ID),
		nonEmpty(fmt.Sprintf("outcomes[%q].statement", o.ID), o.Statement),
		nonEmpty(fmt.Sprintf("outcomes[%q].evidence_rule", o.ID), o.EvidenceRule),
		oneOf(fmt.Sprintf("outcomes[%q].forward_verdict", o.ID), o.ForwardVerdict, forwardVerdicts),
		oneOf(fmt.Sprintf("outcomes[%q].backward_verdict", o.ID), o.BackwardVerdict, backwardVerdicts),
		oneOf(fmt.Sprintf("outcomes[%q].default_disposition", o.ID), o.DefaultDisposition, defaultDispositions),
	} {
		if err != nil {
			return canon.Value{}, err
		}
	}
	if o.Profile == "obligation" && o.Clause == nil {
		return canon.Value{}, fmt.Errorf("outcomes[%q]: profile obligation requires a clause", o.ID)
	}
	members := []canon.Member{
		canon.M("id", canon.Str(o.ID)),
		canon.M("statement", canon.Str(o.Statement)),
		canon.M("evidence_rule", canon.Str(o.EvidenceRule)),
		canon.M("forward_verdict", canon.Str(o.ForwardVerdict)),
		canon.M("backward_verdict", canon.Str(o.BackwardVerdict)),
	}
	// Optional fields are omitted at their engine defaults.
	if o.Tier != "" && o.Tier != "informational" {
		members = append(members, canon.M("tier", canon.Str(o.Tier)))
	}
	if o.Mode != "" && o.Mode != "structural" {
		members = append(members, canon.M("mode", canon.Str(o.Mode)))
	}
	if o.Clause != nil {
		clause := []canon.Member{canon.M("instrument", canon.Str(o.Clause.Instrument)), canon.M("article", canon.Str(o.Clause.Article))}
		if o.Clause.Paragraph != nil {
			clause = append(clause, canon.M("paragraph", canon.Str(*o.Clause.Paragraph)))
		}
		if o.Clause.Jurisdiction != nil {
			clause = append(clause, canon.M("jurisdiction", canon.Str(*o.Clause.Jurisdiction)))
		}
		members = append(members, canon.M("clause", canon.Obj(clause...)))
	}
	if o.Profile != "" && o.Profile != "outcome" {
		members = append(members, canon.M("profile", canon.Str(o.Profile)))
	}
	return canon.Obj(members...), nil
}

// Party roles: the side of the deal the user is on, as capsule-cli seals it
// on the deal's opening intent (x-deal-v0 intent.party_role). Absent means
// buyer: every deal sealed before the role was recorded is a buyer's.
const (
	PartyBuyer  = "buyer"
	PartySeller = "seller"
)

// KnownPartyRole is whether role is one capsule-cli seals ("" is absent,
// a buyer's deal). Which pack version a caller checks each role against is
// the caller's choice.
func KnownPartyRole(role string) bool {
	return role == "" || role == PartyBuyer || role == PartySeller
}

// UnknownPartyRoleError is a party_role that is not one KnownPartyRole knows.
type UnknownPartyRoleError struct{ Role string }

func (e *UnknownPartyRoleError) Error() string {
	return fmt.Sprintf("party_role %q is not one this plugin knows (%s, %s)", e.Role, PartyBuyer, PartySeller)
}
