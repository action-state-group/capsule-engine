package admission

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"slices"
	"strings"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// The admission pack profile v0 (evidencebook-skills#51): a pack-source.yaml
// with scope.kind admission, compiled to pack-definition.json, which is what
// this package reads. The pack states thresholds; what they mean is
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
	// Digest is the definition_digest: the SHA-256 of the
	// pack-definition.json bytes as loaded, which the decision cites beside
	// pack_id.
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

// sourceFile is pack-definition.json: the pack source as evidencebook-skills'
// pack_compile.py compiles it, every optional field written out. A pointer
// marks a number that may be 0 but not absent (noNulls has refused every
// null).
type sourceFile struct {
	Format      string   `json:"format"`
	PackID      string   `json:"pack_id"`
	PackVersion string   `json:"pack_version"`
	Source      string   `json:"source"`
	Outcomes    []string `json:"outcomes"`
	Missing     string   `json:"missing"`
	Accept      map[string]struct {
		Band  string `json:"band"`
		Scale *int   `json:"scale"`
	} `json:"accept"`
	Tiers []struct {
		ID        string         `json:"id"`
		MaxDecade map[string]int `json:"max_decade"`
	} `json:"tiers"`
	Budget struct {
		MaxAsks   *int   `json:"max_asks"`
		DeadlineS *int64 `json:"deadline_s"`
	} `json:"budget"`
	TrustRoots struct {
		Controllers []string `json:"controllers"`
		Witnesses   []string `json:"witnesses"`
		Judges      []string `json:"judges"`
	} `json:"trust_roots"`
	Rows []struct {
		ID        string           `json:"id"`
		Statement string           `json:"statement"`
		Source    string           `json:"source"`
		Check     string           `json:"check"`
		Params    map[string]int64 `json:"params"`
		From      string           `json:"from"`
		OnFail    json.RawMessage  `json:"on_fail"`
		Ask       *struct {
			Derivation string `json:"derivation"`
		} `json:"ask"`
	} `json:"rows"`
}

// CompiledFormat is the format a pack-definition.json names.
const CompiledFormat = "admission-pack/v0"

var (
	rowIDPattern  = regexp.MustCompile(`^[a-z][a-z0-9_-]*$`)
	packIDPattern = regexp.MustCompile(`^[a-z][a-z0-9_]*/[a-z][a-z0-9_-]*/\d+\.\d+\.\d+$`)
)

// Load reads an admission pack from its pack-definition.json bytes, the
// pinned artifact pack_compile.py writes from a pack-source.yaml. The
// evaluator reads no YAML: two YAML readers read one source two ways, so the
// compiler's is the only one. The bytes must be exactly the JCS (RFC 8785)
// encoding of their own parse, which refuses duplicate keys, other
// whitespace or key order and non-integer numbers; they are then decoded
// strictly. A pinned pack is loaded with LoadPinned, which also checks the
// digest.
func Load(src []byte) (*Pack, error) {
	v, err := canon.FromJSON(src)
	if err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	if jcs, err := canon.JCS(v); err != nil || jcs != string(src) {
		return nil, errors.New("admission pack: the bytes are not the JCS encoding of their own parse")
	}
	if err := noNulls(src); err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	if err := exactKeys(src); err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	var f sourceFile
	dec := json.NewDecoder(bytes.NewReader(src))
	// An unknown key is refused, so no weight, score or other field the
	// evaluator does not read can ride in a pack.
	dec.DisallowUnknownFields()
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("admission pack: %w", err)
	}
	sum := sha256.Sum256(src)
	p := &Pack{ID: f.PackID, Version: f.PackVersion, Digest: hex.EncodeToString(sum[:]), Missing: f.Missing}
	var errs []error
	bad := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }

	if f.Format != CompiledFormat {
		bad("format must be %s, got %q", CompiledFormat, f.Format)
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
		if *u.Scale < -scaleMax || *u.Scale > scaleMax {
			bad("accept.%s: scale %d must be from %d to %d", cur, *u.Scale, -scaleMax, scaleMax)
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
		decades := map[string]int{}
		for _, band := range sortedKeys(t.MaxDecade) {
			d := t.MaxDecade[band]
			decades[band] = d
			if d < -decadeMax || d > decadeMax {
				bad("tier %s: max_decade.%s %d must be from %d to %d", t.ID, band, d, -decadeMax, decadeMax)
			}
			if !bands[band] {
				bad("tier %s: max_decade.%s is not a band unit in accept", t.ID, band)
			}
			if prev, ok := below[band]; ok && d <= prev {
				bad("tier %s: max_decade.%s %d does not rise above the tier below (%d)", t.ID, band, d, prev)
			}
			below[band] = d
		}
		p.Tiers = append(p.Tiers, Tier{ID: t.ID, MaxDecade: decades})
	}
	// A unit no tier names would leave a deal in it with no tier at all.
	for _, band := range sortedKeys(bands) {
		if _, named := below[band]; !named {
			bad("accept: band unit %s is named by no tier's max_decade", band)
		}
	}

	if f.Budget.MaxAsks == nil || *f.Budget.MaxAsks < 0 || *f.Budget.MaxAsks > maxAsksMax {
		bad("budget.max_asks must be from 0 to %d", maxAsksMax)
	} else {
		p.MaxAsks = *f.Budget.MaxAsks
	}
	if f.Budget.DeadlineS == nil || *f.Budget.DeadlineS <= 0 || *f.Budget.DeadlineS > deadlineSMax {
		bad("budget.deadline_s must be from 1 to %d", deadlineSMax)
	} else {
		p.DeadlineS = *f.Budget.DeadlineS
	}
	for _, k := range []struct {
		kind  string
		names []string
	}{{"controllers", f.TrustRoots.Controllers}, {"witnesses", f.TrustRoots.Witnesses}, {"judges", f.TrustRoots.Judges}} {
		for _, n := range k.names {
			if strings.TrimSpace(n) == "" {
				bad("trust_roots.%s: a name must not be empty", k.kind)
			}
		}
	}
	p.TrustRoots = TrustRoots{Controllers: f.TrustRoots.Controllers, Witnesses: f.TrustRoots.Witnesses, Judges: f.TrustRoots.Judges}

	seen := map[string]bool{}
	if len(f.Rows) == 0 {
		bad("rows must not be empty")
	}
	for i, r := range f.Rows {
		where := fmt.Sprintf("rows[%d] (%s)", i, r.ID)
		if !rowIDPattern.MatchString(r.ID) {
			bad("%s: id must be lowercase letters, digits, - or _", where)
		}
		if seen[r.ID] {
			bad("%s: id is listed twice", where)
		}
		seen[r.ID] = true
		row := Row{ID: r.ID, Statement: r.Statement, Source: r.Source, Check: r.Check, Params: map[string]int64{}}
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
				} else {
					row.Params[k] = v
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
			if json.Unmarshal(r.OnFail, &c) != nil || len(c) != 1 || c["cap"] == "" {
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
			if json.Unmarshal(r.OnFail, &o) != nil {
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

// scaleMax and decadeMax bound an accept scale and a tier ceiling, so a
// band's decade (digits + scale) stays far inside an int.
const (
	scaleMax  = 30
	decadeMax = 60
	// maxAsksMax and deadlineSMax bound the budget, as the lint does.
	maxAsksMax   = 1000
	deadlineSMax = 100 * 365 * 24 * 3600
)

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

// The member names each object of a pack-definition.json may have.
// encoding/json matches a field name case-insensitively ("Statement" would
// fill statement), which the Rust loader does not, so every name is checked
// exactly first.
var (
	topKeys    = []string{"format", "pack_id", "pack_version", "source", "outcomes", "missing", "accept", "tiers", "budget", "trust_roots", "rows"}
	unitKeys   = []string{"band", "scale"}
	tierKeys   = []string{"id", "max_decade"}
	budgetKeys = []string{"max_asks", "deadline_s"}
	rootKeys   = []string{"controllers", "witnesses", "judges"}
	rowKeys    = []string{"id", "statement", "source", "check", "params", "from", "on_fail", "ask"}
	askKeys    = []string{"derivation"}
)

// exactKeys refuses a member name that is not exactly one the format names.
func exactKeys(src []byte) error {
	check := func(where string, raw json.RawMessage, allowed []string) (map[string]json.RawMessage, error) {
		var m map[string]json.RawMessage
		if len(raw) == 0 || string(raw) == "null" || json.Unmarshal(raw, &m) != nil {
			return nil, nil // not an object: the strict decode reports it
		}
		for k := range m {
			if !slices.Contains(allowed, k) {
				return nil, fmt.Errorf("%s: unknown field %q", where, k)
			}
		}
		return m, nil
	}
	top, err := check("pack", src, topKeys)
	if err != nil {
		return err
	}
	var accept map[string]json.RawMessage
	_ = json.Unmarshal(top["accept"], &accept)
	for cur, u := range accept {
		if _, err := check("accept."+cur, u, unitKeys); err != nil {
			return err
		}
	}
	if _, err := check("budget", top["budget"], budgetKeys); err != nil {
		return err
	}
	if _, err := check("trust_roots", top["trust_roots"], rootKeys); err != nil {
		return err
	}
	var tiers, rows []json.RawMessage
	_ = json.Unmarshal(top["tiers"], &tiers)
	for i, t := range tiers {
		if _, err := check(fmt.Sprintf("tiers[%d]", i), t, tierKeys); err != nil {
			return err
		}
	}
	_ = json.Unmarshal(top["rows"], &rows)
	for i, r := range rows {
		m, err := check(fmt.Sprintf("rows[%d]", i), r, rowKeys)
		if err != nil {
			return err
		}
		if _, err := check(fmt.Sprintf("rows[%d].ask", i), m["ask"], askKeys); err != nil {
			return err
		}
	}
	return nil
}

// noNulls refuses a null anywhere: no member of the format is nullable, and
// a null would otherwise decode to a zero value (an empty trust-root name, a
// threshold of 0).
func noNulls(src []byte) error {
	dec := json.NewDecoder(bytes.NewReader(src))
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if tok == nil {
			return errors.New("null is not a pack value")
		}
	}
}
