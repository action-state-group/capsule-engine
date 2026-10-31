package admission

import (
	"fmt"
	"slices"
	"time"
)

// Inputs is what was received about a counterparty, as the caller verified
// it, plus the deal. Nothing here is fetched or verified by the evaluator:
// signatures, witness receipts and the staple's presenter are checked before
// a card reaches Inputs (admission design §3), and Now is the caller's clock.
type Inputs struct {
	Now time.Time
	// Amount is the deal's amount as a plain decimal in Currency; "" when the
	// deal states none, which is a missing input.
	Amount   string
	Currency string
	Platform Platform
	Card     Card
	// Own is this node's own history with the counterparty; nil when it has
	// not been read.
	Own      *OwnHistory
	Operator Operator
	// Vouches are verified third-party vouches; Subject is the
	// counterparty's own controller, whose vouches do not count.
	Vouches []Vouch
	Subject string
	// Stake is collateral posted on this deal; nil when none is.
	Stake     *Stake
	Selection Selection
	// Judged holds a judged row's result by row id; absent is not judged. A
	// result from a judge outside the pack's trust roots is not counted.
	Judged map[string]Judgement
	// Asks holds the state of an ask already made, by its derivation
	// (history_card/1).
	Asks map[string]string
	// AsksMade is how many asks this counterparty has had in the pack's
	// budget window.
	AsksMade int
}

// Platform holds the platform's own facts about the counterparty.
type Platform struct {
	// ReleaseAttestation is "valid", "invalid" or "" when unknown.
	ReleaseAttestation string
	// OwnerSigned is nil when unknown.
	OwnerSigned *bool
}

// Card and selection states (admission design §5).
const (
	CardVerified     = "verified"
	CardUnverifiable = "unverifiable"
	CardRefused      = "refused"
	CardUnanswered   = "unanswered"
)

// Card is the counterparty's history_card/1 or served_summary/1 as the
// caller verified it. A zero Card is absent.
type Card struct {
	State              string
	FirstCheckpointAt  time.Time
	LatestCheckpointAt time.Time
	// Witness is the witness whose receipt on the latest checkpoint the
	// caller verified, or "".
	Witness string
	// Controller is the controller the card's key declares itself bound to,
	// or "" when it declares none.
	Controller string
	// Claims are the claims the card holds, with their denominator; nil when
	// the card carries no counts (history_card/1 carries none today).
	Claims *Claims
	// Bands are the card's per-band counts; nil when the card carries none,
	// and empty when it carries them and every band is empty. A
	// standard_history/1 card carries them (StandardHistoryCounts).
	Bands []BandCount
	// Disputes are the card's received disputes, open out of all; nil when
	// the card carries no dispute tally. StandardHistoryCounts fills Bands
	// and Disputes from a standard_history/1 card.
	Disputes *Disputes
}

// Disputes are the disputes others raised about the counterparty that its
// card shows: those still open, out of all of them.
type Disputes struct {
	Open int64 `json:"open"`
	Of   int64 `json:"of"`
}

// BandCount is a card's count in one value band (Reference v3 §3A.14): the
// corroborated, net-settled subjects whose net amount falls in Decade of
// Currency, each subject banded once after netting, and how many of them
// were resolved against the counterparty.
type BandCount struct {
	Currency string `json:"currency"`
	Decade   int    `json:"decade"`
	Subjects int64  `json:"subjects"`
	Adverse  int64  `json:"adverse"`
}

// Claims are a card's claims held out of claims made, in its window.
type Claims struct {
	Held, Total int64
}

// Selection is a bound selection: the counterparty's answer to our own ask,
// over our nonce, with inclusion proofs, as the caller verified it. A zero
// Selection is absent.
type Selection struct {
	State string
	// UnansweredDisputes are the times of disputes with anyone that the
	// selection shows left unanswered.
	UnansweredDisputes []time.Time
}

// Judgement is a judged row's result and the judge that gave it.
type Judgement struct {
	Met   bool
	Judge string
}

// OwnHistory is this node's own book about the counterparty, as counts.
type OwnHistory struct {
	Exchanges   int64
	Settled     int64
	HeldHalves  int64
	Conflicting int64
	Verdicts    []Verdict
}

// Verdict is one adjudication about the counterparty this node holds.
type Verdict struct {
	At      time.Time
	Adverse bool
}

// Operator is what the operator's own config says about the counterparty.
type Operator struct {
	DenyListed bool
	// AllowListed is nil when the operator keeps no allow list, which
	// refuses no one.
	AllowListed *bool
}

// Stake is collateral posted on a deal, as an amount and currency. It is a
// row source only, with no bond terms (#51).
type Stake struct {
	Amount   string
	Currency string
	// Rail is the adapter declaration of the rail the stake is posted on;
	// a stake counts only on a rail the pack's trust roots pin.
	Rail RailPin
}

// Vouch is one verified vouch: the controller its signer is bound to.
type Vouch struct {
	Controller string
}

// Ask states for an ask already made.
const (
	AskPending    = "pending"
	AskRefused    = "refused"
	AskUnanswered = "unanswered"
	AskAnswered   = "answered"
)

// Row results, as the plugin's admission_decision rows read.
const (
	Pass    = "pass"
	Fail    = "fail"
	NotRead = "not_read"
)

// RowResult is one row as the decision records it.
type RowResult struct {
	Row    string
	Result string
	// Count and Denominator are set on a row that counts.
	Count, Denominator *int64
	// Effect is what the row did to the decision: "none", NO, LIMIT, ASK or
	// "cap".
	Effect string
	// held is set on a not_read row whose input was there but held nothing
	// to read (no verdict yet about the counterparty): not a missing input.
	held bool
	// earned is set on a ladder row that read the card's bands and found
	// one earned: the highest decade, in the deal's band unit, the earned
	// history covers (the earned band plus the step).
	earned *int
}

// AskOut is one Evidence Request a decision derives from a not_read row.
type AskOut struct {
	Row        string
	Derivation string
	// DeadlineS is the pack's budget.deadline_s: after it, an unanswered ask
	// is recorded as absence, never failure (design §8).
	DeadlineS int64
}

// Decision is an admission decision.
type Decision struct {
	Verdict string
	// Source is what decided: the row id, or one of SourceAboveTier,
	// SourceMissing, SourceRows.
	Source string
	// Tier is the tier admitted at, or "" when there is none.
	Tier string
	// Band is nil for a free deal or one whose band could not be read.
	Band *Band
	Rows []RowResult
	Asks []AskOut
	Pack PackRef
}

// PackRef is the pack a decision cites.
type PackRef struct {
	ID     string
	Digest string
}

// Decision sources that are not a row.
const (
	// SourceAboveTier: the deal's band is above the pack's top tier.
	SourceAboveTier = "above_tier"
	// SourceMissing: an input the decision needed was missing, and the pack's
	// missing default decided.
	SourceMissing = "missing"
	// SourceRows: the rows together, with none deciding alone.
	SourceRows = "rows"
)

// Evaluate decides admission under p. It is deterministic in its inputs.
//
// The resolution (admission design §2, §4; capsule-engine#265):
//  1. the deal's band gives its tier; a free deal is in the lowest tier,
//     and a band above every tier is above_tier;
//  2. each row that applies to the deal's tier reads pass, fail or
//     not_read;
//  3. a failing row whose on_fail is NO decides NO; a not_read one gives
//     the pack's missing default;
//  4. the tier admitted starts at the deal's tier and drops below a LIMIT
//     row's tier when that row fails or is not read (a ladder row, only to
//     the tier its earned history holds), and to a judged row's cap when it
//     fails;
//  5. GO when the tier admitted holds the deal; otherwise ASK when a
//     LIMIT row that did not pass carries an ask not yet made and the
//     budget allows; otherwise LIMIT at the tier admitted. A refused,
//     unanswered or pending ask limits and never bans.
//
// The missing default is LIMIT at the lowest tier or NO.
//
// An outcome the pack does not yield is never produced: the loader refuses
// rows that could produce one, and where only the evaluator could (above
// the top tier, or below the lowest), the pack's missing default decides.
func Evaluate(p *Pack, in Inputs) Decision {
	d := Decision{Pack: PackRef{ID: p.ID, Digest: p.Digest}}
	top := len(p.Tiers) - 1

	dealTier, aboveTier, missing := 0, false, false
	if band, ok, err := ValueBand(in.Amount, in.Currency, p.Accept); err != nil {
		missing = true
	} else if ok {
		d.Band = &band
		dealTier = -1
		for i, t := range p.Tiers {
			if ceil, has := t.MaxDecade[band.Currency]; has && band.Decade <= ceil {
				dealTier = i
				break
			}
		}
		if dealTier < 0 {
			// Above every tier that names the unit: the deal is held to the
			// highest of them, the most this pack offers in that unit.
			aboveTier = true
			for i, t := range p.Tiers {
				if _, has := t.MaxDecade[band.Currency]; has {
					dealTier = i
				}
			}
			top = dealTier
		}
	}

	admitted := dealTier
	var noRow, missingRow string
	// askRows index d.Rows: the LIMIT rows that did not pass and carry an
	// ask.
	var askRows []int
	rowOf := map[string]Row{}
	for _, r := range p.Rows {
		rowOf[r.ID] = r
		if r.From > dealTier {
			continue
		}
		res := readRow(r, in, p.Accept, p.TrustRoots)
		res.Effect = "none"
		switch {
		case r.Source == SourceJudged:
			if res.Result == Fail && r.Cap < admitted {
				admitted, res.Effect = r.Cap, "cap"
			}
		case r.OnFail == Record:
		case r.OnFail == NO:
			if res.Result == Fail && noRow == "" {
				noRow, res.Effect = r.ID, NO
			} else if res.Result == NotRead && !res.held && missingRow == "" {
				missingRow = r.ID
			}
		case r.OnFail == LIMIT:
			if res.Result != Pass {
				res.Effect = LIMIT
				to := r.From - 1
				// A ladder row limits to the tier its earned history holds,
				// when that is above the tier below its own (Reference v3
				// §3A.14): history earns a limit, not a pass or a floor.
				if res.earned != nil && d.Band != nil {
					to = max(to, earnedTier(p, d.Band.Currency, *res.earned, dealTier))
				}
				if to < admitted {
					admitted = to
				}
				// A stale or missing card is asked for again (admission design
				// §4, §8): a failing row asks as a not_read one does.
				if r.Ask != "" {
					askRows = append(askRows, len(d.Rows))
				}
			}
		}
		d.Rows = append(d.Rows, res)
	}

	tierID := func(i int) string {
		if i < 0 {
			return ""
		}
		return p.Tiers[i].ID
	}
	decide := func(verdict, source string, tier int) Decision {
		// Only above_tier can reach an outcome the pack does not yield (the
		// loader refuses rows that could). The missing default decides it,
		// and the source still names the cause.
		if verdict != NO && !contains(p.Outcomes, verdict) {
			verdict = p.Missing
		}
		switch verdict {
		case NO:
			tier = -1
		case LIMIT:
			if tier < 0 {
				tier = 0
			}
		}
		d.Verdict, d.Source, d.Tier = verdict, source, tierID(tier)
		return d
	}
	switch {
	case noRow != "":
		return decide(NO, noRow, -1)
	case missing:
		return decide(p.Missing, SourceMissing, -1)
	case missingRow != "":
		return decide(p.Missing, missingRow, -1)
	}
	// Above the top tier: once no NO row and no missing input decided, and no
	// row lowered the tier, the decision is LIMIT at the top tier. A lowered
	// tier goes on to ASK or LIMIT like any other deal.
	if aboveTier && admitted >= top {
		return decide(LIMIT, SourceAboveTier, top)
	}
	if admitted >= dealTier {
		return decide(GO, SourceRows, dealTier)
	}
	budget := p.MaxAsks - in.AsksMade
	for _, j := range askRows {
		r := rowOf[d.Rows[j].Row]
		if _, made := in.Asks[r.Ask]; made {
			continue
		}
		// One ask answers every row that needs its derivation.
		dup := false
		for _, a := range d.Asks {
			dup = dup || a.Derivation == r.Ask
		}
		if !dup {
			if budget <= 0 {
				continue
			}
			budget--
			d.Asks = append(d.Asks, AskOut{Row: r.ID, Derivation: r.Ask, DeadlineS: p.DeadlineS})
		}
		d.Rows[j].Effect = ASK
	}
	if len(d.Asks) > 0 {
		return decide(ASK, SourceRows, admitted)
	}
	// admitted is never below the lowest tier: the loader refuses a LIMIT
	// row there, and a cap names a tier.
	return decide(LIMIT, SourceRows, admitted)
}

// earnedTier is the highest tier, up to dealTier, that a limit of decade
// limit in unit holds whole: every tier up to it that names the unit has a
// ceiling at or below limit. It is -1 when the lowest such tier's ceiling
// is already above limit. It reaches dealTier only for a deal above the top
// tier, whose own tier's ceiling is below the deal.
func earnedTier(p *Pack, unit string, limit, dealTier int) int {
	for i := 0; i <= dealTier; i++ {
		if ceil, has := p.Tiers[i].MaxDecade[unit]; has && ceil > limit {
			return i - 1
		}
	}
	return dealTier
}

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

// predicate is one admission check: the source it reads and the params it
// takes. The list is closed and versioned with the evaluator.
type predicate struct {
	source string
	params []string
}

var predicates = map[string]predicate{
	"platform.attestation_not_invalid": {SourcePlatform, nil},
	"platform.owner_signed":            {SourcePlatform, nil},
	"card.verified":                    {SourceCard, nil},
	"card.fresh":                       {SourceCard, []string{"max_age_s"}},
	"card.key_age":                     {SourceCard, []string{"min_days"}},
	"card.witnessed":                   {SourceCard, nil},
	"card.claims_held":                 {SourceCard, []string{"min"}},
	"card.controller_declared":         {SourceCard, nil},
	"card.controller_trusted":          {SourceCard, nil},
	"bound.selection_verified":         {SourceBound, nil},
	"bound.no_unanswered_dispute":      {SourceBound, []string{"window_days"}},
	"own.settled":                      {SourceOwnHistory, []string{"min"}},
	"own.held_halves":                  {SourceOwnHistory, []string{"min"}},
	"own.no_conflicting":               {SourceOwnHistory, nil},
	"own.no_adverse_adjudication":      {SourceOwnHistory, []string{"window_days"}},
	"operator.not_deny_listed":         {SourceOperator, nil},
	"operator.allow_listed":            {SourceOperator, nil},
	"vouch.distinct_controllers":       {SourceVouch, []string{"min"}},
	"stake.covers_deal":                {SourceStake, nil},
	"card.band_history_or_stake":       {SourceCard, []string{"k", "step_decades", "max_adverse_permille"}},
	"card.open_disputes":               {SourceCard, []string{"max"}},
	"judged":                           {SourceJudged, nil},
}

// stakeCovers reports whether the stake covers the deal: in the same band
// unit, its amount is at least the deal's. It is not read (read false) when
// there is no stake, when its rail is not pinned in the trust roots (an
// undeclared adapter is NOT_CHECKED, never a pass; AAC#406), or when a stake
// or deal amount does not read or is in another unit.
func stakeCovers(in Inputs, accept map[string]Unit, roots TrustRoots) (covers, read bool) {
	if in.Stake == nil || !slices.Contains(roots.Rails, in.Stake.Rail) {
		return false, false
	}
	stake, err1 := quantity(in.Stake.Amount, in.Stake.Currency, accept)
	deal, err2 := quantity(in.Amount, in.Currency, accept)
	if err1 != nil || err2 != nil || stake.unit != deal.unit {
		return false, false
	}
	return stake.atLeast(deal), true
}

// bandDecadeMax bounds a card band's decade; a band outside it is not
// counted.
const bandDecadeMax = 1000

// ladder reads the per-band ladder with its two roads (Reference v3
// §3A.14): limit = max(earned band × step-up, collateral on this deal). The
// earned band is the highest decade, in the deal's band unit, in which the
// card shows at least k subjects with at most max_adverse_permille of them
// resolved against the counterparty; the step-up is step_decades decades.
// It passes when the deal's decade is within the earned band plus the step,
// or when a stake on a pinned rail covers the deal. Card bands map through
// accept as amounts do, and bands that map to one decade of one unit are
// added; a currency outside accept, or a band whose counts do not read, is
// not counted.
func ladder(r Row, in Inputs, accept map[string]Unit, roots TrustRoots) RowResult {
	if covers, _ := stakeCovers(in, accept, roots); covers {
		return RowResult{Result: Pass}
	}
	deal, ok, err := ValueBand(in.Amount, in.Currency, accept)
	if err != nil || !ok || in.Card.State != CardVerified || in.Card.Bands == nil {
		return RowResult{Result: NotRead}
	}
	type sums struct{ subjects, adverse int64 }
	byDecade := map[int]sums{}
	for _, b := range in.Card.Bands {
		u, found := accept[b.Currency]
		if !found || u.Band != deal.Currency || b.Decade < -bandDecadeMax || b.Decade > bandDecadeMax ||
			b.Subjects < 0 || b.Adverse < 0 || b.Adverse > b.Subjects || b.Subjects > maxSafeInteger {
			continue
		}
		s := byDecade[b.Decade+u.Scale]
		s.subjects = min(s.subjects+b.Subjects, maxSafeInteger)
		s.adverse = min(s.adverse+b.Adverse, s.subjects)
		byDecade[b.Decade+u.Scale] = s
	}
	k, permille := r.Params["k"], r.Params["max_adverse_permille"]
	var earned *int
	for decade, s := range byDecade {
		// Both products stay inside int64: subjects and adverse are at
		// most 2^53 − 1, and permille at most 1000.
		if s.subjects >= k && s.adverse*1000 <= permille*s.subjects && (earned == nil || decade > *earned) {
			earned = &decade
		}
	}
	if earned == nil {
		return RowResult{Result: Fail}
	}
	limit := *earned + int(r.Params["step_decades"])
	return RowResult{Result: of(deal.Decade <= limit), earned: &limit}
}

// maxSafeInteger is 2^53 − 1, the largest count every JSON reader keeps.
const maxSafeInteger = 1<<53 - 1

func of(pass bool) string {
	if pass {
		return Pass
	}
	return Fail
}

func counted(res string, count, denom int64) RowResult {
	return RowResult{Result: res, Count: &count, Denominator: &denom}
}

// readRow reads one row. The row's id is set on the result; its effect is
// the caller's.
func readRow(r Row, in Inputs, accept map[string]Unit, roots TrustRoots) RowResult {
	res := func() RowResult {
		switch r.Check {
		case "platform.attestation_not_invalid":
			switch in.Platform.ReleaseAttestation {
			case "":
				return RowResult{Result: NotRead}
			case "invalid":
				return RowResult{Result: Fail}
			default:
				return RowResult{Result: Pass}
			}
		case "platform.owner_signed":
			if in.Platform.OwnerSigned == nil {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(*in.Platform.OwnerSigned)}
		case "card.verified":
			switch in.Card.State {
			case CardVerified:
				return RowResult{Result: Pass}
			case CardUnverifiable:
				return RowResult{Result: Fail}
			}
			return RowResult{Result: NotRead}
		case "card.fresh":
			if in.Card.State != CardVerified {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(Fresh(in.Card.LatestCheckpointAt, in.Now, time.Duration(r.Params["max_age_s"])*time.Second))}
		case "card.key_age":
			if in.Card.State != CardVerified {
				return RowResult{Result: NotRead}
			}
			// A card that states no first checkpoint shows no age.
			if in.Card.FirstCheckpointAt.IsZero() {
				return RowResult{Result: Fail}
			}
			minAge := time.Duration(r.Params["min_days"]) * 24 * time.Hour
			return RowResult{Result: of(!in.Card.FirstCheckpointAt.After(in.Now.Add(-minAge)))}
		case "card.witnessed":
			if in.Card.State != CardVerified {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(in.Card.Witness != "" && contains(roots.Witnesses, in.Card.Witness))}
		case "card.claims_held":
			if in.Card.State != CardVerified || in.Card.Claims == nil {
				return RowResult{Result: NotRead}
			}
			c := in.Card.Claims
			return counted(of(c.Held >= r.Params["min"]), c.Held, c.Total)
		case "card.controller_declared":
			if in.Card.State != CardVerified {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(in.Card.Controller != "")}
		case "card.controller_trusted":
			if in.Card.State != CardVerified {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(in.Card.Controller != "" && contains(roots.Controllers, in.Card.Controller))}
		case "bound.selection_verified":
			switch in.Selection.State {
			case CardVerified:
				return RowResult{Result: Pass}
			case CardUnverifiable:
				return RowResult{Result: Fail}
			}
			return RowResult{Result: NotRead}
		case "bound.no_unanswered_dispute":
			if in.Selection.State != CardVerified {
				return RowResult{Result: NotRead}
			}
			since := in.Now.Add(-time.Duration(r.Params["window_days"]) * 24 * time.Hour)
			var open int64
			for _, at := range in.Selection.UnansweredDisputes {
				if !at.Before(since) && !at.After(in.Now) {
					open++
				}
			}
			return RowResult{Result: of(open == 0), Count: &open}
		case "own.settled":
			if in.Own == nil {
				return RowResult{Result: NotRead}
			}
			return counted(of(in.Own.Settled >= r.Params["min"]), in.Own.Settled, in.Own.Exchanges)
		case "own.held_halves":
			if in.Own == nil {
				return RowResult{Result: NotRead}
			}
			return counted(of(in.Own.HeldHalves >= r.Params["min"]), in.Own.HeldHalves, in.Own.Exchanges)
		case "own.no_conflicting":
			if in.Own == nil {
				return RowResult{Result: NotRead}
			}
			return counted(of(in.Own.Conflicting == 0), in.Own.Conflicting, in.Own.Exchanges)
		case "own.no_adverse_adjudication":
			// not_read until this node holds a verdict about the counterparty
			// (the plugin's disputes row, base/1).
			if in.Own == nil {
				return RowResult{Result: NotRead}
			}
			if len(in.Own.Verdicts) == 0 {
				return RowResult{Result: NotRead, held: true}
			}
			since := in.Now.Add(-time.Duration(r.Params["window_days"]) * 24 * time.Hour)
			var adverse, inWindow int64
			for _, v := range in.Own.Verdicts {
				if v.At.Before(since) || v.At.After(in.Now) {
					continue
				}
				inWindow++
				if v.Adverse {
					adverse++
				}
			}
			return counted(of(adverse == 0), adverse, inWindow)
		case "operator.not_deny_listed":
			return RowResult{Result: of(!in.Operator.DenyListed)}
		case "operator.allow_listed":
			// With no allow list kept, nothing is refused by one.
			if in.Operator.AllowListed == nil {
				return RowResult{Result: Pass}
			}
			return RowResult{Result: of(*in.Operator.AllowListed)}
		case "vouch.distinct_controllers":
			// Distinct controllers, the counterparty's own excluded; a
			// voucher's weight is its own card, read elsewhere (D-26).
			distinct := map[string]bool{}
			for _, v := range in.Vouches {
				if v.Controller != "" && v.Controller != in.Subject {
					distinct[v.Controller] = true
				}
			}
			n := int64(len(distinct))
			return counted(of(n >= r.Params["min"]), n, int64(len(in.Vouches)))
		case "stake.covers_deal":
			covers, read := stakeCovers(in, accept, roots)
			if !read {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(covers)}
		case "card.band_history_or_stake":
			return ladder(r, in, accept, roots)
		case "card.open_disputes":
			// Open disputes stand beside the ladder, never inside its
			// adverse share (evidencebook-skills#87).
			if in.Card.State != CardVerified || in.Card.Disputes == nil {
				return RowResult{Result: NotRead}
			}
			c := in.Card.Disputes
			return counted(of(c.Open <= r.Params["max"]), c.Open, c.Of)
		case "judged":
			// A judge outside the trust roots is reported, not counted
			// (SDK-SURFACE evaluate, NOT_CHECKED).
			j, ok := in.Judged[r.ID]
			if !ok || !contains(roots.Judges, j.Judge) {
				return RowResult{Result: NotRead}
			}
			return RowResult{Result: of(j.Met)}
		}
		panic(fmt.Sprintf("admission: check %q passed the loader but has no reader", r.Check))
	}()
	res.Row = r.ID
	return res
}
