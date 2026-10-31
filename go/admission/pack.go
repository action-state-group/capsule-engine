package admission

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"

	"gopkg.in/yaml.v3"
)

// The admission pack profile v0 (evidencebook-skills#51): a pack-source.yaml
// with scope.kind admission. The pack states thresholds; what they mean is
// the evaluator's (Evaluate). The loader refuses a pack that breaks the
// admission design rules (capsule-engine#265), so a pack cannot opt out of
// them: only platform, own_history and operator rows decide NO, a judged row
// only caps a tier, a missing input never defaults to GO, and a pack has no
// field a weight or score could hide in.

// Outcomes, in the order a decision ranks them.
const (
	GO    = "GO"
	ASK   = "ASK"
	LIMIT = "LIMIT"
	NO    = "NO"
)

// Record is a row's on_fail when its result is sealed and decides nothing,
// as the Mesh base rows are (Steven, 10-29, u137).
const Record = "record"

// Row sources (admission design §3). Each has a ceiling on what it decides.
const (
	SourcePlatform   = "platform"
	SourceCard       = "card"
	SourceOwnHistory = "own_history"
	SourceOperator   = "operator"
	SourceVouch      = "vouch"
	SourceStake      = "stake"
	// SourceBound is an artifact the counterparty returned to our own ask,
	// signed over our nonce: a selection with inclusion proofs (design §3).
	SourceBound  = "bound"
	SourceJudged = "judged"
)

// mayDecideNO are the sources whose failure may be NO: platform facts, our
// own adjudicated history and the operator's config (Steven, 10-29).
var mayDecideNO = []string{SourcePlatform, SourceOwnHistory, SourceOperator}

// Pack is a loaded admission pack.
type Pack struct {
	ID      string
	Version string
	// Digest is the SHA-256 of the pack-source.yaml bytes as loaded: the
	// bytes the decision cites (pack_id@version plus this digest).
	Digest   string
	Outcomes []string
	Missing  string
	Accept   map[string]Unit
	Tiers    []Tier
	MaxAsks  int
	// DeadlineS is how long, in seconds, an ask this pack derives waits for
	// an answer before it is recorded as unanswered.
	DeadlineS  int64
	TrustRoots TrustRoots
	Rows       []Row
}

// TrustRoots are the pack's trust roots (Reference v3 §3A.2 item 5): the
// controllers, witnesses and judges whose word this pack counts. An empty
// list trusts none.
type TrustRoots struct {
	Controllers []string
	Witnesses   []string
	Judges      []string
}

// Tier is one rung of the pack's ladder: a deal sits in the first tier
// whose ceiling for its band currency holds the band's decade.
type Tier struct {
	ID        string
	MaxDecade map[string]int
}

// Row is one admission row.
type Row struct {
	ID        string
	Statement string
	Source    string
	Check     string
	Params    map[string]int64
	// From is the index of the lowest tier the row applies to.
	From int
	// OnFail is NO, LIMIT or Record; for a judged row it is empty and Cap
	// is set.
	OnFail string
	// Cap is the index of the tier a failing judged row caps the deal at.
	Cap int
	// Ask is the Evidence Request derivation a not_read row asks for, or "".
	Ask string
}

type sourceFile struct {
	PackID      string `yaml:"pack_id"`
	PackVersion string `yaml:"pack_version"`
	Scope       struct {
		Kind string `yaml:"kind"`
	} `yaml:"scope"`
	Source   string   `yaml:"source"`
	Outcomes []string `yaml:"outcomes"`
	Missing  string   `yaml:"missing"`
	Accept   map[string]struct {
		Band  string `yaml:"band"`
		Scale *int   `yaml:"scale"`
	} `yaml:"accept"`
	Tiers []struct {
		ID        string         `yaml:"id"`
		MaxDecade map[string]int `yaml:"max_decade"`
	} `yaml:"tiers"`
	Budget struct {
		MaxAsks   *int   `yaml:"max_asks"`
		DeadlineS *int64 `yaml:"deadline_s"`
	} `yaml:"budget"`
	TrustRoots struct {
		Controllers []string `yaml:"controllers"`
		Witnesses   []string `yaml:"witnesses"`
		Judges      []string `yaml:"judges"`
	} `yaml:"trust_roots"`
	Rows []struct {
		ID        string           `yaml:"id"`
		Statement string           `yaml:"statement"`
		Source    string           `yaml:"source"`
		Check     string           `yaml:"check"`
		Params    map[string]int64 `yaml:"params"`
		From      string           `yaml:"from"`
		OnFail    yaml.Node        `yaml:"on_fail"`
		Ask       *struct {
			Derivation string `yaml:"derivation"`
		} `yaml:"ask"`
	} `yaml:"rows"`
}

var (
	rowIDPattern  = regexp.MustCompile(`^[a-z][a-z0-9_-]*$`)
	packIDPattern = regexp.MustCompile(`^[a-z][a-z0-9_]*/[a-z][a-z0-9_-]*/\d+\.\d+\.\d+$`)
)

// Load reads an admission pack from its pack-source.yaml bytes. A pinned
// pack is loaded with LoadPinned, which also checks the digest.
func Load(src []byte) (*Pack, error) {
	var doc yaml.Node
	if err := yaml.Unmarshal(src, &doc); err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	if v := yaml11Boolean(&doc); v != "" {
		// YAML 1.1 readers (PyYAML, so the pack's own lint) read a plain NO as
		// false; this reader (YAML 1.2) reads it as "NO". A pack reads the
		// same in both or not at all.
		return nil, fmt.Errorf("admission pack: the plain scalar %q is a boolean in YAML 1.1; quote it", v)
	}
	if err := plainScalars(&doc); err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	var f sourceFile
	dec := yaml.NewDecoder(bytes.NewReader(src))
	// An unknown key is refused, so no weight, score or other field the
	// evaluator does not read can ride in a pack.
	dec.KnownFields(true)
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	sum := sha256.Sum256(src)
	p := &Pack{ID: f.PackID, Version: f.PackVersion, Digest: hex.EncodeToString(sum[:]), Missing: f.Missing}
	var errs []error
	bad := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }

	if f.Scope.Kind != "admission" {
		bad("scope.kind must be admission, got %q", f.Scope.Kind)
	}
	if !packIDPattern.MatchString(f.PackID) {
		bad("pack_id %q is not owner/name/version", f.PackID)
	} else if want := "/" + f.PackVersion; len(f.PackID) < len(want) || f.PackID[len(f.PackID)-len(want):] != want {
		bad("pack_id %q does not end in pack_version %q", f.PackID, f.PackVersion)
	}

	if len(f.Outcomes) == 0 {
		bad("outcomes must name the outcomes this pack yields")
	}
	for _, o := range f.Outcomes {
		if !slices.Contains([]string{GO, ASK, LIMIT, NO}, o) {
			bad("outcomes: %q is not GO, ASK, LIMIT or NO", o)
		}
	}
	if !slices.Contains(f.Outcomes, GO) {
		bad("outcomes must include GO")
	}
	p.Outcomes = f.Outcomes
	switch {
	case f.Missing == GO:
		bad("missing must not be GO: a missing input is never a silent GO")
	case !slices.Contains([]string{LIMIT, NO}, f.Missing):
		// LIMIT is to the lowest tier; there is nothing an ASK could name.
		bad("missing must be LIMIT or NO, got %q", f.Missing)
	case !slices.Contains(f.Outcomes, f.Missing):
		bad("missing %s is not in outcomes", f.Missing)
	}

	if len(f.Accept) == 0 {
		bad("accept must name at least one currency")
	}
	p.Accept = map[string]Unit{}
	for _, cur := range sortedKeys(f.Accept) {
		u := f.Accept[cur]
		if u.Band == "" || u.Scale == nil {
			bad("accept.%s needs band and scale", cur)
			continue
		}
		p.Accept[cur] = Unit{Band: u.Band, Scale: *u.Scale}
	}

	bands := map[string]bool{}
	for _, u := range p.Accept {
		bands[u.Band] = true
	}
	if len(f.Tiers) == 0 {
		bad("tiers must not be empty")
	}
	tierIndex := map[string]int{}
	// below holds, per band unit, the ceiling of the highest tier so far
	// that names it, so a unit a tier skips is still compared across the gap.
	below := map[string]int{}
	for i, t := range f.Tiers {
		if t.ID == "" {
			bad("tiers[%d] needs an id", i)
		}
		if _, dup := tierIndex[t.ID]; dup {
			bad("tier %q is listed twice", t.ID)
		}
		tierIndex[t.ID] = i
		for _, band := range sortedKeys(t.MaxDecade) {
			d := t.MaxDecade[band]
			if !bands[band] {
				bad("tier %s: max_decade.%s is not a band unit in accept", t.ID, band)
			}
			if prev, ok := below[band]; ok && d <= prev {
				bad("tier %s: max_decade.%s %d does not rise above the tier below (%d)", t.ID, band, d, prev)
			}
			below[band] = d
		}
		p.Tiers = append(p.Tiers, Tier{ID: t.ID, MaxDecade: t.MaxDecade})
	}
	// A unit no tier names would leave a deal in it with no tier at all.
	for _, band := range sortedKeys(bands) {
		if _, named := below[band]; !named {
			bad("accept: band unit %s is named by no tier's max_decade", band)
		}
	}

	if f.Budget.MaxAsks == nil || *f.Budget.MaxAsks < 0 {
		bad("budget.max_asks must be a non-negative integer")
	} else {
		p.MaxAsks = *f.Budget.MaxAsks
	}
	if f.Budget.DeadlineS == nil || *f.Budget.DeadlineS <= 0 {
		bad("budget.deadline_s must be a positive integer")
	} else {
		p.DeadlineS = *f.Budget.DeadlineS
	}
	p.TrustRoots = TrustRoots{Controllers: f.TrustRoots.Controllers, Witnesses: f.TrustRoots.Witnesses, Judges: f.TrustRoots.Judges}

	seen := map[string]bool{}
	for i, r := range f.Rows {
		where := fmt.Sprintf("rows[%d] (%s)", i, r.ID)
		if !rowIDPattern.MatchString(r.ID) {
			bad("%s: id must be lowercase letters, digits, - or _", where)
		}
		if seen[r.ID] {
			bad("%s: id is listed twice", where)
		}
		seen[r.ID] = true
		row := Row{ID: r.ID, Statement: r.Statement, Source: r.Source, Check: r.Check, Params: r.Params}
		if r.Statement == "" {
			bad("%s: statement is required", where)
		}
		pred, ok := predicates[r.Check]
		switch {
		case !ok:
			bad("%s: check %q is not an admission predicate", where, r.Check)
		case pred.source != r.Source:
			bad("%s: check %s reads %s, not %s", where, r.Check, pred.source, r.Source)
		default:
			for _, k := range pred.params {
				if _, has := r.Params[k]; !has {
					bad("%s: check %s needs params.%s", where, r.Check, k)
				}
			}
			for _, k := range sortedKeys(r.Params) {
				if !slices.Contains(pred.params, k) {
					bad("%s: check %s takes no params.%s", where, r.Check, k)
				} else if v := r.Params[k]; v < 0 || v > paramMax[k] {
					bad("%s: params.%s %d must be from 0 to %d", where, k, v, paramMax[k])
				}
			}
		}
		from, ok := tierIndex[r.From]
		if !ok {
			bad("%s: from %q is not a tier", where, r.From)
		}
		row.From = from

		if r.Source == SourceJudged {
			// A judged row caps a tier and never decides NO (D-23).
			var c map[string]string
			if r.OnFail.Kind != yaml.MappingNode || r.OnFail.Decode(&c) != nil || len(c) != 1 || c["cap"] == "" {
				bad("%s: a judged row's on_fail must be {cap: <tier>}", where)
			} else if idx, ok := tierIndex[c["cap"]]; !ok {
				bad("%s: cap %q is not a tier", where, c["cap"])
			} else if idx >= from {
				// A cap at or above the row's own tier could never lower one.
				bad("%s: cap %s must be a tier below from %s", where, c["cap"], r.From)
			} else {
				row.Cap = idx
			}
			// A cap is a LIMIT: a pack that yields none would turn it into the
			// missing default, a judged row deciding NO (D-23).
			if !slices.Contains(f.Outcomes, LIMIT) {
				bad("%s: a judged row caps a tier, so LIMIT must be in outcomes", where)
			}
		} else {
			var o string
			if r.OnFail.Kind != yaml.ScalarNode || r.OnFail.Decode(&o) != nil {
				bad("%s: on_fail must be NO, LIMIT or record", where)
			}
			switch o {
			case Record:
			case NO:
				if !slices.Contains(mayDecideNO, r.Source) {
					bad("%s: a %s row may not decide NO; only platform, own_history and operator rows do", where, r.Source)
				}
			case LIMIT:
				// The lowest tier is the floor: there is no tier below it to
				// limit to (admission design §4, T0 "LIMIT: never").
				if ok && from == 0 {
					bad("%s: a row from the lowest tier cannot LIMIT; it can decide NO or be recorded", where)
				}
			default:
				bad("%s: on_fail must be NO, LIMIT or record, got %q", where, o)
			}
			if o != Record && o != "" && !slices.Contains(f.Outcomes, o) {
				bad("%s: on_fail %s is not in outcomes", where, o)
			}
			row.OnFail = o
		}
		if r.Ask != nil {
			// Only a LIMIT row asks (resolution step 6); an ask anywhere else
			// would never be made.
			if row.OnFail != LIMIT {
				bad("%s: only a LIMIT row may carry an ask", where)
			}
			if r.Ask.Derivation == "" {
				bad("%s: ask needs a derivation", where)
			}
			if !slices.Contains(f.Outcomes, ASK) {
				bad("%s: carries an ask, but ASK is not in outcomes", where)
			}
			row.Ask = r.Ask.Derivation
		}
		p.Rows = append(p.Rows, row)
	}
	if len(errs) > 0 {
		return nil, fmt.Errorf("admission pack %s: %w", f.PackID, errors.Join(errs...))
	}
	return p, nil
}

// LoadPinned is Load for a pack pinned as pack_id@version plus digest: the
// bytes must be the ones pinned.
func LoadPinned(src []byte, packID, digest string) (*Pack, error) {
	p, err := Load(src)
	if err != nil {
		return nil, err
	}
	if p.ID != packID {
		return nil, fmt.Errorf("admission pack: pinned %s, loaded %s", packID, p.ID)
	}
	if p.Digest != digest {
		return nil, fmt.Errorf("admission pack %s: pinned digest %s, loaded %s", packID, digest, p.Digest)
	}
	return p, nil
}

// The plain scalar rule. YAML readers disagree on which plain scalars are
// strings: yaml.v3 (YAML 1.2) reads 1e3 and 0o10 as numbers, PyYAML (YAML
// 1.1, the pack's lint) reads them as strings, and reads NO, 1:30 and 010 as
// a boolean and integers. So a pack may write a plain (unquoted) scalar only
// in a form every reader reads alike: a plain decimal integer, a dotted
// version (1.0.0), or text that starts with none of the characters numbers,
// timestamps, nulls and merges start with and is not a boolean or null word.
// Anything else is quoted. The lint and the Rust reader hold the same rule.
var (
	decimalInt     = regexp.MustCompile(`^(0|-?[1-9][0-9]*)$`)
	dottedVersion  = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)
	specialLeaders = "0123456789+-.~<"
)

// plainScalars refuses a plain scalar outside the plain scalar rule, and
// anchors, aliases and explicit tags, so a pack reads one way in every
// reader (the lint, this one and the Rust one) or not at all.
func plainScalars(n *yaml.Node) error {
	if n.Anchor != "" || n.Kind == yaml.AliasNode {
		return fmt.Errorf("line %d: anchors and aliases are not read", n.Line)
	}
	if n.Style&yaml.TaggedStyle != 0 {
		return fmt.Errorf("line %d: tags are not read", n.Line)
	}
	if n.Kind == yaml.ScalarNode && n.Style == 0 {
		v := n.Value
		switch {
		case v == "", decimalInt.MatchString(v), dottedVersion.MatchString(v):
		case strings.ContainsRune(specialLeaders, rune(v[0])) || yaml11Booleans[v] || plainWords[v]:
			return fmt.Errorf("line %d: quote %q: YAML readers do not all read it as the same string", n.Line, v)
		}
	}
	for _, c := range n.Content {
		if err := plainScalars(c); err != nil {
			return err
		}
	}
	return nil
}

// plainWords are the boolean and null words YAML 1.2 reads as non-strings,
// beside yaml11Booleans.
var plainWords = map[string]bool{
	"true": true, "True": true, "TRUE": true, "false": true, "False": true, "FALSE": true,
	"null": true, "Null": true, "NULL": true,
}

// yaml11Booleans are the plain scalars YAML 1.1 reads as booleans and YAML
// 1.2 reads as strings (true and false are booleans in both).
var yaml11Booleans = map[string]bool{
	"y": true, "Y": true, "yes": true, "Yes": true, "YES": true, "n": true, "N": true,
	"no": true, "No": true, "NO": true, "on": true, "On": true, "ON": true,
	"off": true, "Off": true, "OFF": true,
}

// yaml11Boolean is the first plain scalar under n that YAML 1.1 and 1.2 read
// differently, or "".
func yaml11Boolean(n *yaml.Node) string {
	if n.Kind == yaml.ScalarNode && n.Style == 0 && yaml11Booleans[n.Value] {
		return n.Value
	}
	for _, c := range n.Content {
		if v := yaml11Boolean(c); v != "" {
			return v
		}
	}
	return ""
}

// paramMax bounds each threshold, so its duration in nanoseconds never wraps:
// a century of seconds or days, and a count no JSON reader loses.
var paramMax = map[string]int64{
	"max_age_s":   100 * 365 * 24 * 3600,
	"min_days":    100 * 365,
	"window_days": 100 * 365,
	"min":         1<<53 - 1,
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}
