package admission

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// StandardHistoryCounts reads the ladder's inputs from a standard_history/1
// card (AAC spec/standard-history-v0.md §4, §5.3; evidencebook-skills#87):
// the card's per-band counts as Card.Bands, and its received disputes as
// Card.Disputes: open out of all, and those decided (for, against, split or
// no_finding) with how many were against the holder (Disputes.Decided,
// evidencebook-skills#280). Only the received side is read: an outcome is
// stated for the holder (§5.4), so a raised dispute decided against it is the
// holder's own complaint rejected. artifact is the card's bytes from an answer the caller has
// verified, card form included (AAC go/standardhistory); this reads only the
// bands and disputes members, and refuses them when they do not read.
//
// The artifact must be what AAC standard-history-v0 §3 says it is, the JCS
// of the card: bytes that are exactly the JCS encoding of their own parse, as
// a pack-definition.json must be (Load). That one rule refuses trailing data,
// duplicate keys, other whitespace or key order, float and -0 forms, and
// strings whose bytes do not round-trip, so Go and Rust read the same bytes
// the same way. Members are then matched by exact key.
//
// A band tally maps to one BandCount: Subjects are its decided deals (held
// plus did_not_hold) and Adverse those that did not hold; undecided deals
// are left out. A band whose decade is null (zero-amount deals) has no band
// and is skipped. The currency is the card's SR assetCode as written, with
// the decade in that asset's own unit, so a pack maps it through accept like
// any other currency (BTC: {band: sat, scale: 8}).
func StandardHistoryCounts(artifact []byte) ([]BandCount, *Disputes, error) {
	return standardHistoryCounts(artifact, "standard_history/1")
}

// StandardHistory3Counts reads the same inputs from a standard_history/3 card
// (AAC spec/standard-history-v0.md §11; evidencebook#461 acceptance 7): its
// bands and disputes members are §4's, so they are read as StandardHistoryCounts
// reads a /1 card's, and a card of any other derivation, /1 and /2 included, is
// refused by its derivation. StandardHistoryCounts refuses a /3 card the same
// way. The /3 members the ladder does not read (outcomes, remedied,
// not_honoured, local_times, coverage_start, not_counted) are not checked here:
// that is the verifier's card_form and denominator (§11.7).
func StandardHistory3Counts(artifact []byte) ([]BandCount, *Disputes, error) {
	return standardHistoryCounts(artifact, "standard_history/3")
}

func standardHistoryCounts(artifact []byte, token string) ([]BandCount, *Disputes, error) {
	card0 := token + " card"
	if err := isJCS(artifact); err != nil {
		return nil, nil, fmt.Errorf("%s: the artifact is not JCS bytes: %w", card0, err)
	}
	// Raw members throughout, decoded into maps: keys match exactly (a struct
	// would match them case-insensitively), and each number is read from its
	// own text.
	var card map[string]json.RawMessage
	if json.Unmarshal(artifact, &card) != nil || card == nil {
		return nil, nil, errors.New(card0 + ": not an object")
	}
	var derivation string
	if raw, ok := card["derivation"]; ok && json.Unmarshal(raw, &derivation) != nil {
		return nil, nil, errors.New(card0 + ": derivation must be a string")
	}
	if derivation != token {
		return nil, nil, fmt.Errorf("%s: derivation is %q", card0, derivation)
	}
	if raw, ok := card["bands"]; !ok || string(raw) == "null" {
		return nil, nil, errors.New(card0 + ": bands is missing")
	}
	var tallies []json.RawMessage
	if json.Unmarshal(card["bands"], &tallies) != nil {
		return nil, nil, errors.New(card0 + ": bands must be an array")
	}
	bands := []BandCount{}
	for i, raw := range tallies {
		var b map[string]json.RawMessage
		if json.Unmarshal(raw, &b) != nil || b == nil {
			return nil, nil, fmt.Errorf("%s: bands[%d] must be an object", card0, i)
		}
		where := fmt.Sprintf("%s: bands[%d]", card0, i)
		var currency string
		if err := json.Unmarshal(b["currency"], &currency); err != nil || currency == "" {
			return nil, nil, fmt.Errorf("%s: currency must be a non-empty string", where)
		}
		var counts [3]int64
		for j, k := range []string{"held", "did_not_hold", "undecided"} {
			n, err := cardCount(b[k])
			if err != nil {
				return nil, nil, fmt.Errorf("%s.%s: %w", where, k, err)
			}
			counts[j] = n
		}
		if string(b["decade"]) == "null" {
			continue
		}
		decade, err := cardInt(b["decade"])
		if err != nil {
			return nil, nil, fmt.Errorf("%s.decade: %w", where, err)
		}
		bands = append(bands, BandCount{Currency: currency, Decade: int(decade),
			Subjects: counts[0] + counts[1], Adverse: counts[1]})
	}
	var disputes, r map[string]json.RawMessage
	// A missing or non-object disputes leaves the map nil, and the check
	// below then refuses it as Rust does.
	_ = json.Unmarshal(card["disputes"], &disputes)
	if json.Unmarshal(disputes["received"], &r) != nil || r == nil {
		return nil, nil, errors.New(card0 + ": disputes.received must be an object")
	}
	of, err := cardCount(r["of"])
	if err != nil {
		return nil, nil, fmt.Errorf("%s: disputes.received.of: %w", card0, err)
	}
	var byOutcome map[string]json.RawMessage
	if json.Unmarshal(r["by_outcome"], &byOutcome) != nil || byOutcome == nil {
		return nil, nil, errors.New(card0 + ": disputes.received.by_outcome must be an object")
	}
	var outcome [5]int64
	for i, k := range []string{"open", "for", "against", "split", "no_finding"} {
		n, err := cardCount(byOutcome[k])
		if err != nil {
			return nil, nil, fmt.Errorf("%s: disputes.received.by_outcome.%s: %w", card0, k, err)
		}
		outcome[i] = n
	}
	// Each count is at most 2^53 − 1, so the sum stays inside int64; it is
	// held to 2^53 − 1 as every card integer is. A verified card's outcomes
	// partition of (§4), so it never reaches that.
	decided := min(outcome[1]+outcome[2]+outcome[3]+outcome[4], maxSafeInteger)
	return bands, &Disputes{Open: outcome[0], Of: of, Decided: &Decided{Against: outcome[2], Of: decided}}, nil
}

// isJCS reports why src is not exactly the JCS encoding of its own parse.
func isJCS(src []byte) error {
	v, err := canon.FromJSON(src)
	if err != nil {
		return err
	}
	if jcs, err := canon.JCS(v); err != nil || jcs != string(src) {
		return errors.New("not the JCS encoding of their own parse")
	}
	return nil
}

// cardInt reads a card integer: a plain JSON integer of at most 2^53 − 1 in
// magnitude (AAC standard-history-v0 §4, "Counts"). The artifact is JCS, so
// an integer is in its one canonical form; a quoted number is not one.
func cardInt(raw json.RawMessage) (int64, error) {
	n, err := strconv.ParseInt(string(raw), 10, 64)
	if err != nil {
		return 0, errors.New("must be an integer")
	}
	if n > maxSafeInteger || n < -maxSafeInteger {
		return 0, errors.New("must be an integer of at most 2^53 - 1 in magnitude")
	}
	return n, nil
}

// cardCount is cardInt for a count, which is 0 or more.
func cardCount(raw json.RawMessage) (int64, error) {
	n, err := cardInt(raw)
	if err == nil && n < 0 {
		err = errors.New("must be 0 or more")
	}
	return n, err
}
